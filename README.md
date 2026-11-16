# Flujo 3 – Optimización operativa (ArquiFusion Corp · Grupo 13)

Una vez al día, Step Functions corre tres ramas en paralelo y envía un reporte por rol:

| Rama | Problema | Qué hace | Dónde corre |
|---|---|---|---|
| A. Combustible | 9 | Detecta máquinas con consumo anormal | Lambda + **Google Cloud Run** (`POST /combustible`) |
| B. Productividad | 10 | Utilización, disponibilidad e índice por máquina, zona y tipo | Solo AWS |
| C. Planificación y repuestos | 4 y 8 | Plan de mantenimiento + cuántos repuestos comprar | Lambda + **Google Cloud Run** (`POST /demanda`) |

Cierre: `ConsolidarReportes` publica en el SNS `TemaReportes` (3 correos filtrados por `rol`: supervisor, logística, gerencia). Si algo falla, Step Functions avisa al supervisor. Consulta por API: `GET /indicadores` y `GET /plan`.

```
flujo3/
├── serverless.yml            infraestructura AWS (tablas, bucket, SNS, Step Functions, EventBridge, API)
├── comun.py                  funciones compartidas (S3, DynamoDB, Parameter Store, llamada a Google)
├── analizar_combustible.py   rama A
├── calcular_productividad.py rama B
├── planificar_mantenimiento.py rama C
├── consolidar_reportes.py    cierre + SNS
├── consultar_indicadores.py  GET /indicadores
├── consultar_plan.py         GET /plan
├── cloudrun/                 servicio de IA para Google Cloud (Flask + IsolationForest)
├── datos/                    generador de datos simulados + cargador a AWS
└── pruebas/prueba_local.py   prueba completa SIN AWS ni Google (simulados en memoria)
```

## 0. Prueba local (gratis, sin cuentas)

```bash
pip install boto3 moto flask scikit-learn numpy
cd datos && python generar_datos.py config.json salida && cd ..
python pruebas/prueba_local.py        # debe terminar en "TODO OK"
```

## 1. Preparar tu máquina

1. Instalar Node.js, Python 3.12, AWS CLI y Serverless Framework (`npm i -g serverless`).
2. `serverless login` (organización **luisnieva**).
3. Credenciales de AWS Academy (Learner Lab → *AWS Details* → *AWS CLI*): pegar las tres líneas
   (`aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`) en `~/.aws/credentials`
   (en Windows: `C:\Users\TU_USUARIO\.aws\credentials`). **Cambian cada vez que abres el laboratorio.**
   Comprobar: `aws sts get-caller-identity`.

## 2. Desplegar AWS

```bash
serverless deploy --param="email=TU_CORREO@dominio.com"
```

* Los tres suscriptores usan ese correo (se puede separar con `--param="emailSupervisor=..." --param="emailLogistica=..." --param="emailGerencia=..."`).
* Llegan 3 correos de **AWS Notifications**: abrir cada uno y confirmar la suscripción.
* Si otra persona del equipo ya creó en esa cuenta el bucket o las tablas compartidas, agregar `--param="crearCompartidas=false"`.
* Al terminar, `serverless info` muestra la URL del API y la API key.

## 3. Cargar los datos simulados

```bash
cd datos
python generar_datos.py config.json salida
python cargar_aws.py
```

Las zonas son las del Flujo 2: **Lima Norte, Lima Centro y Lima Sur**. Sube el historial a `s3://arquifusion-g13-lecturas-<cuenta>/lecturas/AAAA-MM-DD/` y llena `t_maquinas`, `t_mantenimiento`, `t_resultados` y `t_repuestos`.
Todo lo simulado sale de `datos/config.json` (modelos, zonas, rangos de sensores, repuestos…): se cambia ahí, no en el código.

## 4. Google Cloud (Cloud Run)

Necesitas una cuenta de Google (tu Gmail) y que Nicole te agregue como **Editor** del proyecto (IAM). No hace falta crear un proyecto ni poner tarjeta propia.

```bash
gcloud auth login
gcloud config set project ID_DEL_PROYECTO
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
cd cloudrun
gcloud run deploy flujo3-analisis --source . --region us-central1 --allow-unauthenticated --set-env-vars API_KEY=INVENTA_UNA_CLAVE_LARGA
```

Al terminar imprime la **URL del servicio**. Probar: `curl URL/salud` → `{"estado":"ok"}`.
(`--allow-unauthenticated` solo abre la puerta de red; el servicio exige la cabecera `X-API-Key`.)

## 5. Conectar AWS con Google (clave segura, sin tocar el código)

```bash
aws ssm put-parameter --name /arquifusion/gcp/url     --type String       --value "URL_DE_CLOUD_RUN" --overwrite
aws ssm put-parameter --name /arquifusion/gcp/api-key --type SecureString --value "LA_MISMA_CLAVE"  --overwrite
```

Las Lambdas leen esos dos parámetros desde AWS Systems Manager Parameter Store. **La clave nunca va a GitHub.**

## 6. Probar de punta a punta

```bash
aws stepfunctions start-execution --state-machine-arn ARN_DE_FlujoOptimizacionOperativa --input '{"fecha":"2026-11-14"}'
```

(El ARN sale en los *Outputs* del deploy o en la consola de Step Functions.) Deben llegar 3 correos y funcionar:

```bash
curl -H "x-api-key: TU_API_KEY" "https://XXXX.execute-api.us-east-1.amazonaws.com/dev/indicadores?tipo=combustible"
curl -H "x-api-key: TU_API_KEY" "https://XXXX.execute-api.us-east-1.amazonaws.com/dev/plan?tipo=repuesto"
```

Todos los días a las 02:00 (Lima) lo dispara EventBridge (`cron(0 7 * * ? *)`).

## 7. Qué subir a GitHub

Todo el contenido de esta carpeta **menos** lo que ya excluye `.gitignore` (`datos/salida/`, `.serverless/`, claves). Nunca subir las credenciales de AWS ni la API key.

## Contrato con los Flujos 1 y 2 (acordar con el equipo)

| Qué | Dónde / nombre | Quién escribe | Campos clave |
|---|---|---|---|
| Historial de lecturas | `s3://arquifusion-g13-lecturas-<cuenta>/lecturas/AAAA-MM-DD/*.jsonl` (una lectura JSON por línea) | Flujo 1 (Ingesta) | `machine_id, timestamp, lat, lon, temperatura, vibracion, presion, combustible (L/h), carga, velocidad, rpm, horas_uso, estado` |
| `t_maquinas` | PK `machine_id` (sin sufijo `-dev`) | Flujo 1 | `tipo, modelo, zona, horas_uso, intervalo_mant_horas, estado, repuesto_requerido, operador_email` |
| `t_resultados` | PK `machine_id`, SK `timestamp` | Flujo 1 | `prob_falla, puntaje_anomalia, clasificacion, variable_principal` |
| `t_mantenimiento` | PK `machine_id`, SK `registro_id` | carga inicial | `fecha, tipo (preventivo/correctivo), horas_al_momento, falla, causa, componente` |
| `t_repuestos` | PK `zona`, SK `repuesto_id` (igual que el Flujo 2) | carga inicial / Flujo 2 | `nombre, stock` (+ `componente`, opcional: si falta se deduce del `repuesto_id`) |
| `t_pedidos_repuesto` | PK `pedido_id`, índice `EstadoFechaIndex` (`estado`, `fecha`) | **Flujo 2** (`SolicitarRepuesto`) | `repuesto_id, cantidad, estado, fecha, zona, incidente_id, machine_id`. Estados: `PENDIENTE`, `EN_PROCESO` (el Flujo 3 los suma como oferta futura) y `ATENDIDO` (ya no cuenta) |
| `t_indicadores`, `t_plan` | PK `fecha`, SK `clave` | **Flujo 3** | — |

No hay SNS ni SQS compartidos con el Flujo 2: **no se necesitan ARN**, solo los nombres de tablas y bucket de esta tabla (siempre que todo viva en la misma cuenta de AWS).

## Notas honestas

* `/combustible` usa IsolationForest + z-score robusto (anomalías); `/demanda` es un estimador estadístico (tasa histórica de fallas + riesgo esperado), no un modelo entrenado.
* Los umbrales (`UMBRAL_RIESGO_*`, `UMBRAL_Z`) son provisionales: se calibran con los datos simulados.
* El código se probó con AWS simulado (moto) y Cloud Run local; el primer `serverless deploy` real puede pedir algún ajuste de la cuenta (permisos del LabRole, límites de la cuenta).
