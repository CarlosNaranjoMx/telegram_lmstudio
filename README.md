# Telegram LM Studio Notifier

Un monitor simple para LM Studio que avisa por Telegram cuando el modelo deja de generar.

## Qué hace

- Consulta periódicamente la API de LM Studio en `/api/v1/models`
- Detecta cuando un modelo está generando
- Envía un mensaje a tu bot de Telegram cuando el estado vuelve a idle

## Archivos principales

- `main.py` - script principal
- `.env.example` - plantilla de variables de entorno
- `requirements.txt` - dependencias Python
- `.gitignore` - excluye archivos sensibles y de entorno

## Configuración

1. Crea un bot en Telegram con [@BotFather](https://t.me/BotFather).
2. Copia el token y obtén tu `chat_id` enviando un mensaje al bot y consultando:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Copia `.env.example` a `.env`:

```bash
copy .env.example .env
```

4. Edita `.env` con tu configuración real.

## Ejemplo de `.env`

```ini
LMSTUDIO_URL=http://127.0.0.1:1234
TELEGRAM_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=123456789
POLL_INTERVAL=5
IDLE_CONFIRM=3
```

- `LMSTUDIO_URL`: URL base de tu servidor LM Studio.
- `TELEGRAM_TOKEN`: token de tu bot de Telegram.
- `TELEGRAM_CHAT_ID`: ID del chat donde se enviarán las notificaciones.
- `POLL_INTERVAL`: segundos entre cada comprobación del estado.
- `IDLE_CONFIRM`: número de checks consecutivos en idle antes de notificar.

## Instalación

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

### 1) Ejecutar el monitor de estado
```bash
python main.py
```

### 2) Ejecutar el proxy interceptador
```bash
python proxy.py
```

### 3) Usar el proxy como endpoint
Configura tu cliente o aplicación para usar:

```text
http://127.0.0.1:8080
```

El proxy reenviará las llamadas a tu servidor LM Studio definido en `LMSTUDIO_URL` y puede notificar por Telegram cuando detecte el cierre de una respuesta de inferencia.

### 4) Usar el enfoque CORS
Si tu servidor LM Studio ya tiene CORS habilitado, puedes usar un frontend separado para consultar directamente los endpoints.

- Con CORS activo, una página web puede hacer `fetch()` a LM Studio desde otro origen.
- El proxy también devuelve cabeceras CORS para que puedas usarlo desde un navegador si necesitas interceptar o monitorear las peticiones.

## Nota

No compartas tu archivo `.env` ni tu token de Telegram en repositorios públicos.
