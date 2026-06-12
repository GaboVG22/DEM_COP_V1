# Descarga de DEM COP30 para punto de control de cuenca

Aplicación en **Streamlit** para preparar y descargar un DEM global en formato **GeoTIFF** desde la API `/globaldem` de **OpenTopography**, usando como entrada un punto de control de cuenca en formato **KMZ** o **KML**.

La aplicación **no delimita cuencas**. Su único objetivo es obtener el DEM GeoTIFF necesario para una aplicación posterior de delimitación de cuenca, cálculo de superficie, curvas de nivel y exportación KMZ.

## Funcionalidades

- Carga de archivo `.kmz` o `.kml` con al menos un punto.
- Lectura automática de latitud y longitud.
- Cálculo automático del bounding box:
  - `south`
  - `north`
  - `west`
  - `east`
- Selección del DEM:
  - `COP30` por defecto
  - `NASADEM`
  - `SRTMGL1`
  - `SRTMGL3`
- Construcción de URL de descarga para OpenTopography:

```text
https://portal.opentopography.org/API/globaldem?demtype=COP30&south=...&north=...&west=...&east=...&outputFormat=GTiff&API_Key=...
```

- Campo seguro tipo password para la API Key.
- Visualización de URL con API Key parcialmente oculta.
- Descarga directa del GeoTIFF desde la app.
- Exportación de información de descarga en `.json` y `.txt`.
- Validaciones de punto, API Key, bounding box y área máxima.

## Archivos del proyecto

```text
.
├── app.py
├── requirements.txt
├── README.md
└── .streamlit
    └── config.toml
```

## Despliegue en GitHub y Streamlit Cloud

1. Crear un repositorio nuevo en GitHub.
2. Subir estos archivos al repositorio.
3. Ingresar a Streamlit Cloud.
4. Seleccionar el repositorio.
5. Usar como **Main file path**:

```text
app.py
```

6. Deploy.

## Obtener API Key de OpenTopography

OpenTopography exige API Key para la API Global Datasets. La clave se obtiene desde el portal de OpenTopography, en la sección **My Account** del usuario.

Recomendación: no guardar la API Key en GitHub, no subirla en archivos `.py`, `.txt`, `.json` ni en variables visibles. Esta app pide la clave en pantalla mediante un campo `password` y no la exporta completa.

## Qué significa COP30

`COP30` corresponde al **Copernicus Global DSM 30 m** disponible mediante OpenTopography. Es una fuente global de elevación de aproximadamente 30 m, útil como DEM base para trabajos hidrológicos preliminares y delimitación posterior de cuencas.

## Qué significan south, north, west y east

Son los límites del rectángulo de descarga en coordenadas geográficas WGS84:

- `south`: latitud sur del área.
- `north`: latitud norte del área.
- `west`: longitud oeste del área.
- `east`: longitud este del área.

Para que la solicitud sea válida:

```text
south < north
west  < east
```

## Ejemplo real de prueba

Punto de control:

```text
Latitud:  -30.88173168
Longitud: -71.02085661
```

Bounding box utilizado en prueba previa:

```text
south = -31.20
north = -30.55
west  = -71.40
east  = -70.70
```

URL funcional de referencia:

```text
https://portal.opentopography.org/API/globaldem?demtype=COP30&south=-31.20&north=-30.55&west=-71.40&east=-70.70&outputFormat=GTiff&API_Key=TU_API_KEY
```

## Errores frecuentes

### 204 No Data

OpenTopography no encontró datos para el área solicitada. Posibles soluciones:

- Revisar que el punto esté en tierra.
- Cambiar el DEM.
- Ajustar o ampliar el bounding box.
- Verificar que las coordenadas estén en WGS84.

### 400 Bad Request

La solicitud contiene parámetros inválidos. Revisar:

- `south < north`
- `west < east`
- `demtype` correcto.
- `outputFormat=GTiff`.
- Área solicitada no excesiva.

### 401 Unauthorized

La API Key está vacía, mal escrita o no autorizada. Revisar la clave desde OpenTopography.

## Dependencias

Se usan solo dependencias mínimas:

```text
streamlit
requests
```

No se usan `geopandas`, `rasterio`, `gdal`, `shapely` ni librerías pesadas, porque esta aplicación solo prepara y descarga el DEM. La delimitación de cuenca se realizará en otra aplicación.
