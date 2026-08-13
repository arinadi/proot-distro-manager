# PRoot-Distro Reference (LLM-Friendly)

PRoot-Distro (`pd`) manages rootless Linux containers on Termux and Linux hosts using `proot`. No root, no kernel module, no Docker daemon.

---

## Install

```sh
# Termux
pkg install proot-distro

# Linux host
pip install proot-distro
```

---

## Core Commands

### Install a container

```sh
# From Docker Hub
proot-distro install ubuntu:24.04
proot-distro install debian:bookworm --name my-debian

# From any OCI registry (GHCR, Quay, GitLab, etc.)
proot-distro install ghcr.io/myorg/myimage:latest
proot-distro install quay.io/user/app:1.0

# Private images (set credentials first)
export PD_DOCKER_AUTH=username:password
proot-distro install ghcr.io/myorg/private:latest

# From local tarball (plain rootfs or OCI layout)
proot-distro install ./rootfs.tar.gz --name myapp

# From URL
proot-distro install https://example.com/rootfs.tar.xz --name myapp
```

**Options:**
- `-n, --name NAME` — custom container name
- `-a, --architecture ARCH` — override CPU (`aarch64`, `arm`, `x86_64`)
- `-q, --quiet` — suppress output

### Login (interactive shell)

```sh
proot-distro login ubuntu
proot-distro login ubuntu --user myuser
proot-distro login ubuntu -- bash -c "echo hello"
pd sh ubuntu   # short alias
```

**Options:**
- `-u, --user USER` — login as user (default: root)
- `-b, --bind SRC[:DST]` — bind mount paths
- `--shared-home` — mount host $HOME
- `--isolated` — skip Android host bindings (Termux)
- `-w, --work-dir PATH` — set working directory
- `-e, --env VAR=VAL` — set env var
- `-P, --redirect-ports` — 80→2080, 22→2022, etc.

### Run (execute entrypoint)

```sh
proot-distro run ubuntu           # run image's CMD/ENTRYPOINT
proot-distro run ubuntu -- arg1   # override CMD with args
proot-distro run ubuntu -d        # detach (background)
```

### List containers

```sh
proot-distro list              # installed containers
proot-distro list --image      # cached OCI images
proot-distro list -q           # names only
```

### Session management

```sh
proot-distro ps                # list active sessions
proot-distro kill <PID>        # stop by PID
proot-distro kill ubuntu       # stop all sessions of container
proot-distro kill --all        # stop everything
```

### Container lifecycle

```sh
proot-distro rename old new    # rename container
proot-distro reset ubuntu      # reinstall from image (loses data)
proot-distro remove ubuntu     # delete container permanently
```

### Backup & Restore

```sh
proot-distro backup ubuntu -o ubuntu.tar.gz
proot-distro restore ubuntu.tar.gz
```

### Copy & Sync files

```sh
proot-distro copy ./file.txt ubuntu:/root/file.txt
proot-distro copy ubuntu:/etc/resolv.conf ./bak/

proot-distro sync ./app ubuntu:/opt/app
proot-distro sync --delete ./app ubuntu:/opt/app  # mirror
```

### Build from Dockerfile

```sh
proot-distro build -t myapp:1.0 .
proot-distro build -t myapp:1.0 --install-as myapp .
proot-distro build -t myapp:arm64 --architecture aarch64 .
```

**Supported:** `FROM` (multi-stage), `RUN`, `COPY`, `ADD`, `ENV`, `ARG`, `CMD`, `ENTRYPOINT`, `USER`, `WORKDIR`, `EXPOSE`, `LABEL`, `SHELL`

**Not supported:** `RUN --mount`, `RUN --network`, `COPY --link` (BuildKit-only)

### Push to registry

```sh
export PD_DOCKER_AUTH=username:password
proot-distro push myuser/myapp:1.0
proot-distro push ghcr.io/myorg/myapp:1.0
```

### Clear cache

```sh
proot-distro clear-cache      # delete all cached layers + manifests
```

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `PD_DOCKER_AUTH` | Registry credentials (`user:pass` or `user:token`) |
| `PD_PROOT_BIN` | Custom proot executable path |
| `PROOT_VERBOSE` | Enable proot debug output |
| `TERMUX__PREFIX` | Override Termux prefix (default: `/data/data/com.termux/files/usr`) |
| `TERMUX__HOME` | Override Termux home (default: `/data/data/com.termux/files/home`) |

---

## Storage Layout

| Path | Contents |
|---|---|
| `containers/<name>/rootfs/` | Container filesystem |
| `containers/<name>/manifest.json` | Image ref, arch, OCI manifest |
| `containers/<name>/rootfs/.l2s/` | link2symlink backing store |
| `cache/oci_layers/` | Cached layer blobs |
| `cache/oci_manifests/` | Cached manifests |
| `sessions/<pid>.json` | Active session registry |

**Termux location:** `$PREFIX/var/lib/proot-distro/`
**Linux location:** `~/.local/share/proot-distro/`

---

## Limitations

- **No real root** — proot fakes root via UID/GID remapping. `mount`, `iptables`, real `sudo` won't work.
- **No kernel features** — FUSE, cgroups, namespaces, network isolation are unavailable.
- **No nesting** — cannot run proot inside proot.
- **No systemd** — service supervisors don't work. Run processes directly.
- **Performance** — ptrace overhead makes filesystem-heavy workloads slower.
- **No zstd layers** — Python's tarfile doesn't support zstd. Some newer Docker Hub images fail.
- **Single-arch push** — no manifest list assembly.
- **Cross-arch Termux** — not supported (shared prefix path).
