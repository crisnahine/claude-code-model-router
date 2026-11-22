# fetchapp

A small HTTP client.

## Configuration

- `HTTP_TIMEOUT`: request timeout in seconds (default 30).
- `settings.ini`, section `[http]`, key `timeout`: the same, from a file.
- `stream = on` in `settings.ini` streams every response, which lifts the 25-second cap.
