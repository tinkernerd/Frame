#!/usr/bin/env bash
# Frame — install or update on a Linux server with Docker.
#
#   First time:  git clone <repo> frame && cd frame && ./install.sh
#   Update:      ./install.sh          (pulls the latest code and rebuilds)
#   Re-ask setup questions:  ./install.sh --reconfigure
#   Build from GitHub source instead of downloading the published image:  ./install.sh --build
set -euo pipefail
cd "$(dirname "$0")"

RECONFIGURE=""; BUILD=""
for arg in "$@"; do
  case "$arg" in
    --reconfigure) RECONFIGURE=1 ;;
    --build) BUILD=1 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

ask() {  # ask VAR "Question" "default"
  local var=$1 q=$2 def=${3:-} ans
  if [ -n "$def" ]; then read -r -p "  $q [$def]: " ans; else read -r -p "  $q: " ans; fi
  printf -v "$var" '%s' "${ans:-$def}"
}

current() {  # current value of KEY in .env (empty if unset)
  [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true
}

bold "Frame installer"

# --------------------------------------------------------------- 1. requirements
command -v docker >/dev/null 2>&1 || die "Docker isn't installed. Install it first: https://docs.docker.com/engine/install/  (or: curl -fsSL https://get.docker.com | sh)"
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"
else die "Docker Compose isn't available. Install the docker-compose-plugin package."; fi
docker info >/dev/null 2>&1 || die "Can't talk to Docker. Run with sudo, or add yourself to the docker group: sudo usermod -aG docker \$USER (then log out and back in)."
ok "Docker found ($COMPOSE)"

# --------------------------------------------------------------- 2. latest code
if [ -d .git ] && command -v git >/dev/null 2>&1 && git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
  before=$(git rev-parse HEAD)
  if git pull --ff-only --quiet 2>/dev/null; then
    ok "Code is up to date ($(git rev-parse --short HEAD))"
    # If this script itself just changed, restart with the new version
    if [ "$before" != "$(git rev-parse HEAD)" ] && [ -z "${FRAME_REEXEC:-}" ]; then
      FRAME_REEXEC=1 exec bash "$0" "$@"
    fi
  else warn "Couldn't pull the latest code (local changes?). Continuing with what's here."; fi
fi

# --------------------------------------------------------------- 3. settings
if [ ! -f .env ] || [ -z "$(current MOVIES_PATH)" ] || [ -n "$RECONFIGURE" ]; then
  bold "Setup"
  echo "  Where are your libraries mounted on this server? (e.g. /mnt/nas/movies)"
  ask MOVIES_PATH "Movies folder" "$(current MOVIES_PATH)"
  ask SHOWS_PATH  "Shows folder"  "$(current SHOWS_PATH)"
  for p in "$MOVIES_PATH" "$SHOWS_PATH"; do
    [ -d "$p" ] || die "$p doesn't exist on this server. Mount the NAS share first, then re-run."
  done

  # Default to whoever owns the movie folder, since that's who can write posters there
  OWNER_UID=$(stat -c %u "$MOVIES_PATH"); OWNER_GID=$(stat -c %g "$MOVIES_PATH")
  echo "  Frame runs as a user/group that must be able to write to those folders."
  CUR_UID=$(current PUID); CUR_GID=$(current PGID); CUR_PORT=$(current PORT)
  ask PUID "User ID (owner of $MOVIES_PATH is $OWNER_UID)"  "${CUR_UID:-$OWNER_UID}"
  ask PGID "Group ID (owner of $MOVIES_PATH is $OWNER_GID)" "${CUR_GID:-$OWNER_GID}"
  ask PORT "Port to open Frame on" "${CUR_PORT:-8080}"

  echo "  TMDB key (free: https://www.themoviedb.org/settings/api) — Enter to skip"
  ask TMDB_API_KEY "TMDB API key or read-access token" "$(current TMDB_API_KEY)"
  ask TPDB_USERNAME "ThePosterDB username (credits your downloads) — Enter to skip" "$(current TPDB_USERNAME)"

  umask 077
  cat > .env <<EOF
# Written by install.sh — edit freely, or run ./install.sh --reconfigure
MOVIES_PATH=$MOVIES_PATH
SHOWS_PATH=$SHOWS_PATH
PUID=$PUID
PGID=$PGID
PORT=$PORT
TMDB_API_KEY=$TMDB_API_KEY
TPDB_USERNAME=$TPDB_USERNAME
EOF
  umask 022
  chmod 600 .env  # holds your API key
  ok "Saved settings to .env"
else
  ok "Using existing .env (run ./install.sh --reconfigure to change it)"
fi

set -a; . ./.env; set +a
PORT=${PORT:-8080}; PUID=${PUID:-1000}; PGID=${PGID:-1000}

# --------------------------------------------------------------- 4. permissions
mkdir -p config
if [ "$(stat -c %u config)" != "$PUID" ]; then
  chown "$PUID:$PGID" config 2>/dev/null || sudo chown "$PUID:$PGID" config \
    || warn "Couldn't hand ./config to $PUID:$PGID. TMDB info won't be cached between restarts."
fi
# --------------------------------------------------------------- 5. get image + start
if [ -n "$BUILD" ]; then
  bold "Building Frame from source"
  $COMPOSE up -d --build
elif $COMPOSE pull --quiet frame 2>/dev/null; then
  ok "Downloaded image ghcr.io/tinkernerd/frame:${FRAME_TAG:-latest}"
  bold "Starting Frame"
  $COMPOSE up -d
else
  warn "Couldn't download the published image (none released yet, or it's private). Building from source instead."
  $COMPOSE up -d --build
fi

for d in movies shows; do
  if $COMPOSE exec -T frame sh -c "f=/media/$d/.frame-write-test; touch \"\$f\" && rm \"\$f\"" >/dev/null 2>&1; then
    ok "$d folder is writable"
  else
    warn "$d folder is NOT writable as $PUID:$PGID — posters can be viewed but not uploaded."
    warn "Check the owner with: ls -n <folder>   (and uid=/gid= mount options if it's an SMB share)"
  fi
done

printf '  Waiting for Frame to answer'
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:$PORT/api/config" >/dev/null 2>&1 \
     || wget -qO- "http://127.0.0.1:$PORT/api/config" >/dev/null 2>&1; then
    echo
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    ok "Frame is running → http://${IP:-<server-ip>}:$PORT"
    echo "  Logs: $COMPOSE logs -f frame    Update later: ./install.sh"
    exit 0
  fi
  printf '.'; sleep 2
done
echo
warn "Frame didn't answer within 90 seconds. Recent logs:"
$COMPOSE logs --tail 40 frame
exit 1
