# Linux / NAS deployment

The repository root contains a portable `compose.yaml`. Run from the repository root:

```sh
mkdir -p data
docker compose up -d --build
```

Open `http://<host-ip>:18772/`, configure the weather location and device in the web console, then connect.

The container uses host networking because the ZECTRIX SDK communicates over UDP port 32342. It runs read-only and keeps writable state only in `./data` and `/tmp`.

Set `NOTE4_UID` and `NOTE4_GID` to the user that owns `./data`. To expose a local photo library, mount read-only paths with an override:

```yaml
services:
  note4:
    environment:
      NOTE4_PHOTO_INDEX: /photo-library/index.json
      NOTE4_PHOTO_ROOT: /photo-library/photos
    volumes:
      - /absolute/photo/library:/photo-library:ro
```

The index and photo directory must be readable by the container user. Web upload remains available when no mount is configured.

Do not expose port 18772 directly to the internet. The local console has same-origin and request-size protections, but no remote-user authentication.
