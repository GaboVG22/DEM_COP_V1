"""Streamlit app para descargar DEM global desde OpenTopography.

Objetivo: leer un punto de control desde KMZ/KML, calcular un bounding box
hidrologicamente razonable y descargar un DEM GeoTIFF mediante la API /globaldem.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests
import streamlit as st

API_BASE_URL = "https://portal.opentopography.org/API/globaldem"
SUPPORTED_DEMS = ["COP30", "NASADEM", "SRTMGL1", "SRTMGL3"]
DEFAULT_DEM = "COP30"
DEFAULT_TIMEOUT = (10, 180)  # segundos: conexion, lectura
MAX_ABSOLUTE_AREA_KM2 = 450_000  # limite oficial aprox. para datasets globales de 30 m


def mask_api_key(api_key: str) -> str:
    """Devuelve una version segura/mascarada de la API Key."""
    api_key = (api_key or "").strip()
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


def build_url(params: dict[str, Any]) -> str:
    """Construye la URL final para OpenTopography."""
    return f"{API_BASE_URL}?{urlencode(params)}"


def is_point_tag(element: ET.Element) -> bool:
    return element.tag.lower().endswith("point")


def is_coordinates_tag(element: ET.Element) -> bool:
    return element.tag.lower().endswith("coordinates")


def parse_coordinate_text(text: str | None) -> tuple[float, float] | None:
    """Lee texto KML tipo 'lon,lat,alt lon,lat,alt' y retorna (lat, lon)."""
    if not text:
        return None

    tokens = re.split(r"\s+", text.strip())
    for token in tokens:
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return None


def parse_kml_bytes(kml_bytes: bytes) -> tuple[float, float]:
    """Extrae el primer punto desde un KML. Prioriza geometria <Point>."""
    try:
        root = ET.fromstring(kml_bytes)
    except ET.ParseError as exc:
        raise ValueError("El archivo KML no es XML valido o esta danado.") from exc

    # 1) Priorizar coordenadas que esten dentro de un <Point>.
    for point in root.iter():
        if not is_point_tag(point):
            continue
        for child in point.iter():
            if is_coordinates_tag(child):
                coordinate = parse_coordinate_text(child.text)
                if coordinate:
                    return coordinate

    # 2) Respaldo: primer bloque de coordenadas encontrado.
    for element in root.iter():
        if is_coordinates_tag(element):
            coordinate = parse_coordinate_text(element.text)
            if coordinate:
                return coordinate

    raise ValueError("El KMZ/KML no contiene un punto con coordenadas validas.")


def extract_kml_from_kmz(kmz_bytes: bytes) -> tuple[bytes, str]:
    """Extrae doc.kml o el primer KML disponible dentro de un KMZ."""
    try:
        with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as zf:
            kml_names = [name for name in zf.namelist() if name.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError("El archivo KMZ no contiene ningun archivo .kml interno.")
            preferred = next((name for name in kml_names if name.lower().endswith("doc.kml")), kml_names[0])
            return zf.read(preferred), preferred
    except zipfile.BadZipFile as exc:
        raise ValueError("El archivo KMZ no es un ZIP valido o esta danado.") from exc


def read_control_point(uploaded_file: Any) -> tuple[float, float, str]:
    """Lee un punto de control desde KMZ o KML cargado en Streamlit."""
    filename = uploaded_file.name
    raw = uploaded_file.getvalue()
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if suffix == "kmz":
        kml_bytes, internal_name = extract_kml_from_kmz(raw)
        lat, lon = parse_kml_bytes(kml_bytes)
        return lat, lon, f"{filename} / {internal_name}"

    if suffix == "kml":
        lat, lon = parse_kml_bytes(raw)
        return lat, lon, filename

    raise ValueError("Formato no compatible. Debe cargar un archivo .kmz o .kml.")


def margin_to_degrees(lat: float, margin_value: float, margin_unit: str) -> tuple[float, float]:
    """Convierte margen a grados de latitud y longitud."""
    if margin_value <= 0:
        raise ValueError("El margen debe ser mayor que cero.")

    if margin_unit == "Grados decimales":
        return margin_value, margin_value

    # Aproximacion suficiente para definir cajas de descarga DEM.
    lat_delta = margin_value / 111.32
    cos_lat = max(abs(math.cos(math.radians(lat))), 0.01)
    lon_delta = margin_value / (111.32 * cos_lat)
    return lat_delta, lon_delta


def calculate_bbox(lat: float, lon: float, margin_value: float, margin_unit: str) -> dict[str, float]:
    """Calcula south/north/west/east a partir del punto y margen."""
    lat_delta, lon_delta = margin_to_degrees(lat, margin_value, margin_unit)

    south = max(-90.0, lat - lat_delta)
    north = min(90.0, lat + lat_delta)
    west = max(-180.0, lon - lon_delta)
    east = min(180.0, lon + lon_delta)

    return {
        "south": round(south, 6),
        "north": round(north, 6),
        "west": round(west, 6),
        "east": round(east, 6),
    }


def estimate_bbox_area_km2(bbox: dict[str, float]) -> float:
    """Estima el area aproximada del bounding box en km2."""
    mean_lat = (bbox["south"] + bbox["north"]) / 2
    height_km = abs(bbox["north"] - bbox["south"]) * 111.32
    width_km = abs(bbox["east"] - bbox["west"]) * 111.32 * abs(math.cos(math.radians(mean_lat)))
    return max(0.0, height_km * width_km)


def validate_bbox(bbox: dict[str, float], max_area_km2: float) -> list[str]:
    """Valida coherencia y tamano del bounding box."""
    errors: list[str] = []

    if bbox["south"] >= bbox["north"]:
        errors.append("El bounding box no es valido: south debe ser menor que north.")
    if bbox["west"] >= bbox["east"]:
        errors.append("El bounding box no es valido: west debe ser menor que east.")

    area_km2 = estimate_bbox_area_km2(bbox)
    if area_km2 <= 0:
        errors.append("El area estimada del bounding box debe ser mayor que cero.")
    if area_km2 > max_area_km2:
        errors.append(
            f"El area solicitada ({area_km2:,.0f} km²) supera el maximo configurado "
            f"({max_area_km2:,.0f} km²). Reduzca el margen."
        )
    if area_km2 > MAX_ABSOLUTE_AREA_KM2:
        errors.append(
            f"El area solicitada ({area_km2:,.0f} km²) supera el limite absoluto de seguridad "
            f"({MAX_ABSOLUTE_AREA_KM2:,.0f} km²)."
        )

    return errors


def make_filename(dem_type: str, lat: float, lon: float) -> str:
    """Nombre ordenado para el GeoTIFF descargado."""
    return f"DEM_{dem_type}_lat_{lat:.6f}_lon_{lon:.6f}.tif"


def looks_like_geotiff(content: bytes, content_type: str) -> bool:
    """Verifica si la respuesta parece ser GeoTIFF y no una pagina de error."""
    if not content:
        return False
    tiff_magic = content.startswith(b"II*\x00") or content.startswith(b"MM\x00*")
    binary_type = any(token in content_type.lower() for token in ["tiff", "geotiff", "octet-stream", "application/x-tiff"])
    html_or_json = content.lstrip().startswith((b"<", b"{", b"["))
    return (tiff_magic or binary_type) and not html_or_json


def safe_response_preview(response: requests.Response, limit: int = 500) -> str:
    """Muestra una porcion pequena del error sin exponer datos sensibles."""
    text = response.text if response.text else ""
    text = re.sub(r"API_Key=[^&\s]+", "API_Key=****", text)
    return text[:limit]


def download_dem(params: dict[str, Any]) -> tuple[bytes | None, str | None]:
    """Descarga el DEM y retorna bytes o mensaje de error."""
    try:
        response = requests.get(API_BASE_URL, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.Timeout:
        return None, "La solicitud excedio el tiempo de espera. Reduzca el area o intente nuevamente."
    except requests.RequestException as exc:
        return None, f"Error de conexion con OpenTopography: {exc}"

    if response.status_code == 204:
        return None, "204 No Data: OpenTopography no encontro datos para el area solicitada. Revise el punto, el DEM o amplie/modifique el bounding box."
    if response.status_code == 400:
        return None, f"400 Bad Request: revise south/north/west/east, demtype y outputFormat. Detalle: {safe_response_preview(response)}"
    if response.status_code == 401:
        return None, "401 Unauthorized: API Key no autorizada o incorrecta. Verifique su clave de OpenTopography."
    if response.status_code != 200:
        return None, f"Error HTTP {response.status_code}: {safe_response_preview(response)}"

    content_type = response.headers.get("Content-Type", "")
    if not looks_like_geotiff(response.content, content_type):
        return None, "La respuesta fue HTTP 200, pero no parece ser un GeoTIFF valido. Detalle: " + safe_response_preview(response)

    return response.content, None


def build_download_info(
    lat: float,
    lon: float,
    dem_type: str,
    bbox: dict[str, float],
    area_km2: float,
    masked_url: str,
    margin_value: float,
    margin_unit: str,
) -> dict[str, Any]:
    return {
        "fecha_generacion_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "punto_control": {"latitud": lat, "longitud": lon},
        "dem_seleccionado": dem_type,
        "margen": {"valor": margin_value, "unidad": margin_unit},
        "bounding_box": bbox,
        "area_aproximada_km2": round(area_km2, 3),
        "url_descarga_mascarada": masked_url,
        "nota_seguridad": "La API Key no se guarda ni se exporta completa.",
    }


def info_to_txt(info: dict[str, Any]) -> str:
    bbox = info["bounding_box"]
    point = info["punto_control"]
    margin = info["margen"]
    return "\n".join(
        [
            "DESCARGA DE DEM DESDE OPENTOPOGRAPHY",
            "====================================",
            f"Fecha generacion UTC: {info['fecha_generacion_utc']}",
            f"Latitud punto control: {point['latitud']}",
            f"Longitud punto control: {point['longitud']}",
            f"DEM seleccionado: {info['dem_seleccionado']}",
            f"Margen: {margin['valor']} {margin['unidad']}",
            "",
            "Bounding box:",
            f"  south: {bbox['south']}",
            f"  north: {bbox['north']}",
            f"  west : {bbox['west']}",
            f"  east : {bbox['east']}",
            f"Area aproximada: {info['area_aproximada_km2']} km2",
            "",
            "URL de descarga mascarada:",
            info["url_descarga_mascarada"],
            "",
            info["nota_seguridad"],
        ]
    )


st.set_page_config(
    page_title="Descarga DEM COP30",
    page_icon="🛰️",
    layout="wide",
)

st.title("Descarga de DEM COP30 para punto de control de cuenca")
st.caption(
    "Aplicación técnica para preparar y descargar un DEM GeoTIFF desde OpenTopography "
    "a partir de un punto de control ingresado en KMZ/KML."
)

with st.sidebar:
    st.header("Parámetros")

    uploaded_file = st.file_uploader(
        "Cargar punto de control KMZ/KML",
        type=["kmz", "kml"],
        help="El archivo debe contener al menos una geometría tipo Point.",
    )

    use_example = st.checkbox(
        "Usar punto de ejemplo sin cargar KMZ/KML",
        value=False,
        help="Útil para probar la app con el punto real trabajado anteriormente.",
    )

    dem_type = st.selectbox(
        "DEM OpenTopography",
        SUPPORTED_DEMS,
        index=SUPPORTED_DEMS.index(DEFAULT_DEM),
        help="COP30 queda por defecto para análisis hidrológico preliminar de 30 m.",
    )

    margin_unit = st.radio("Unidad del margen", ["Grados decimales", "Kilómetros"], horizontal=False)

    if margin_unit == "Grados decimales":
        margin_value = st.number_input(
            "Margen alrededor del punto",
            min_value=0.01,
            max_value=5.0,
            value=0.35,
            step=0.05,
            format="%.2f",
            help="Ejemplo: 0,25°, 0,50° o 1,00°. El margen se aplica hacia norte, sur, este y oeste.",
        )
    else:
        margin_value = st.number_input(
            "Margen alrededor del punto",
            min_value=1.0,
            max_value=300.0,
            value=40.0,
            step=5.0,
            format="%.1f",
            help="Se convierte internamente a grados usando una aproximación geográfica.",
        )

    max_area_km2 = st.number_input(
        "Área máxima permitida por la app (km²)",
        min_value=10.0,
        max_value=float(MAX_ABSOLUTE_AREA_KM2),
        value=10_000.0,
        step=1_000.0,
        help="Control de seguridad para evitar descargas demasiado grandes.",
    )

    api_key = st.text_input(
        "API Key OpenTopography",
        type="password",
        help="La clave no se guarda, no se exporta completa y no se imprime en pantalla.",
    )

    st.divider()
    st.markdown("**Streamlit Cloud**")
    st.code("Main file path: app.py", language="text")

lat: float | None = None
lon: float | None = None
source_label = ""

if use_example:
    lat = -30.88173168
    lon = -71.02085661
    source_label = "Punto de ejemplo integrado"
elif uploaded_file is not None:
    try:
        lat, lon, source_label = read_control_point(uploaded_file)
    except ValueError as exc:
        st.error(str(exc))

left, right = st.columns([1.1, 0.9])

with left:
    st.subheader("1. Punto detectado")
    if lat is None or lon is None:
        st.info("Cargue un archivo KMZ/KML con un punto de control o active el punto de ejemplo.")
    else:
        st.success("Punto de control leído correctamente.")
        st.write(f"**Fuente:** {source_label}")
        coord_col1, coord_col2 = st.columns(2)
        coord_col1.metric("Latitud", f"{lat:.8f}")
        coord_col2.metric("Longitud", f"{lon:.8f}")

with right:
    st.subheader("2. Instrucciones rápidas")
    with st.expander("¿Dónde obtener la API Key?", expanded=True):
        st.markdown(
            "Ingrese al portal de OpenTopography, cree/inicie sesión y solicite una API Key "
            "desde **My Account**. Use su propia clave y no la publique en GitHub."
        )
    with st.expander("¿Qué significa COP30?"):
        st.markdown(
            "**COP30** corresponde al Copernicus Global DSM de 30 m disponible desde "
            "OpenTopography. Es un modelo digital global útil como base para análisis "
            "hidrológico preliminar y delimitación posterior de cuencas."
        )
    with st.expander("¿Qué son south, north, west y east?"):
        st.markdown(
            "Son los límites del rectángulo de descarga en coordenadas geográficas WGS84: "
            "**south** es latitud sur, **north** es latitud norte, **west** es longitud oeste "
            "y **east** es longitud este."
        )

if lat is not None and lon is not None:
    bbox = calculate_bbox(lat, lon, float(margin_value), margin_unit)
    area_km2 = estimate_bbox_area_km2(bbox)
    validation_errors = validate_bbox(bbox, float(max_area_km2))

    params_without_key: dict[str, Any] = {
        "demtype": dem_type,
        "south": bbox["south"],
        "north": bbox["north"],
        "west": bbox["west"],
        "east": bbox["east"],
        "outputFormat": "GTiff",
    }
    params_with_key = {**params_without_key, "API_Key": api_key.strip()}
    masked_params = {**params_without_key, "API_Key": mask_api_key(api_key)}
    masked_url = build_url(masked_params)
    filename = make_filename(dem_type, lat, lon)

    st.divider()
    st.subheader("3. Bounding box calculado")

    bbox_cols = st.columns(5)
    bbox_cols[0].metric("south", f"{bbox['south']:.6f}")
    bbox_cols[1].metric("north", f"{bbox['north']:.6f}")
    bbox_cols[2].metric("west", f"{bbox['west']:.6f}")
    bbox_cols[3].metric("east", f"{bbox['east']:.6f}")
    bbox_cols[4].metric("Área aprox.", f"{area_km2:,.0f} km²")

    if validation_errors:
        for error in validation_errors:
            st.error(error)
    else:
        st.success("Bounding box válido para construir la solicitud.")

    st.subheader("4. URL de descarga")
    st.caption("La API Key se muestra parcialmente oculta por seguridad.")
    st.code(masked_url, language="text")

    info = build_download_info(
        lat=lat,
        lon=lon,
        dem_type=dem_type,
        bbox=bbox,
        area_km2=area_km2,
        masked_url=masked_url,
        margin_value=float(margin_value),
        margin_unit=margin_unit,
    )

    info_col1, info_col2 = st.columns(2)
    info_col1.download_button(
        "Descargar información JSON",
        data=json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="info_descarga_dem.json",
        mime="application/json",
    )
    info_col2.download_button(
        "Descargar información TXT",
        data=info_to_txt(info).encode("utf-8"),
        file_name="info_descarga_dem.txt",
        mime="text/plain",
    )

    st.subheader("5. Descarga directa del GeoTIFF")
    st.write(f"**Nombre sugerido:** `{filename}`")

    can_download = not validation_errors
    if not api_key.strip():
        st.warning("Ingrese una API Key de OpenTopography para habilitar la descarga directa.")
        can_download = False

    if st.button("Solicitar DEM GeoTIFF a OpenTopography", disabled=not can_download, type="primary"):
        with st.spinner("Consultando OpenTopography y preparando el GeoTIFF..."):
            dem_bytes, error = download_dem(params_with_key)
        if error:
            st.error(error)
        elif dem_bytes:
            st.session_state["dem_bytes"] = dem_bytes
            st.session_state["dem_filename"] = filename
            st.success(f"DEM descargado correctamente. Tamaño: {len(dem_bytes) / (1024 * 1024):.2f} MB")

    if "dem_bytes" in st.session_state and "dem_filename" in st.session_state:
        st.download_button(
            "Guardar DEM GeoTIFF",
            data=st.session_state["dem_bytes"],
            file_name=st.session_state["dem_filename"],
            mime="image/tiff",
        )

    st.divider()
    st.subheader("Solución de errores frecuentes")
    err1, err2, err3 = st.columns(3)
    with err1:
        st.markdown(
            "**204 No Data**  \n"
            "No hay datos para el área o el DEM seleccionado. Revise que el punto esté en tierra, "
            "cambie el DEM o ajuste/amplíe el bounding box."
        )
    with err2:
        st.markdown(
            "**400 Bad Request**  \n"
            "La solicitud contiene parámetros inválidos. Revise que south < north, west < east, "
            "que el demtype exista y que outputFormat sea GTiff."
        )
    with err3:
        st.markdown(
            "**401 Unauthorized**  \n"
            "La API Key está vacía, mal escrita, vencida o no autorizada. Genere o revise su clave "
            "en OpenTopography."
        )
else:
    st.divider()
    st.subheader("Ejemplo de prueba")
    st.markdown(
        "Puede activar **Usar punto de ejemplo sin cargar KMZ/KML** para probar con el punto real trabajado:\n\n"
        "- Latitud: `-30.88173168`\n"
        "- Longitud: `-71.02085661`\n"
        "- Bounding box de referencia: `south=-31.20`, `north=-30.55`, `west=-71.40`, `east=-70.70`"
    )
