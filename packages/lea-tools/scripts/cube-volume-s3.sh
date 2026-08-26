#!/usr/bin/env bash
# cube-volume-s3.sh — CubeSandbox Volume Plugin for S3-compatible storage
#
# 通用 S3 兼容 Volume 插件。开发环境接 MinIO，生产环境接华为云 OBS / 腾讯云 COS /
# AWS S3 等任何 S3 兼容后端。同一份脚本，靠 volume-s3.conf 里的 ENDPOINT 切换。
#
# 参考自官方 examples/volume/cos/binary/cube-volume-cos.sh，改造点：
#   cosfs         → s3fs-fuse
#   coscmd        → s3fs 短挂 bucket 根，touch/rm 目录（不装 python/pip/CLI 依赖）
#   密钥文件格式  → s3fs 标准 <ACCESS>:<SECRET>（去掉 cosfs 的 bucket 前缀）
#   endpoint      → 从 conf 读，不再硬编码
#
# 为什么不走 curl --aws-sigv4：Ubuntu 22.04 自带 curl 7.81 的 sigv4 实现跟 MinIO
# 组合概率性签不上（8.0+ 才修好）。既然 attach 阶段 s3fs 无论如何都要用，
# create/destroy 也走 s3fs 短挂，减少一个依赖 + 一处凭证出错点。
#
# 4 个 hook：
#   create  — CubeMaster 侧；在 bucket 里建 volume 目录（写 .keep 对象）
#   destroy — CubeMaster 侧；从 bucket 删除 volume 目录（递归删）
#   attach  — Cubelet 侧；用 s3fs 挂载 <bucket>:/volumes/<id> 到 <base>/s3-<id>
#   detach  — Cubelet 侧；无沙箱再用时 fusermount -u
#
# CLI 调用（CubeMaster/Cubelet fork）:
#   cube-volume-s3 --op <op> [--<key> <value> ...]
# 输出：单行 JSON 到 stdout；成功 exit 0，失败非零 exit + JSON 里带 error。
#
# 配置文件（同目录 volume-s3.conf，或 $CUBE_S3_CONFIG）：
#   ENDPOINT=http://127.0.0.1:9000
#   BUCKET=lea-sandbox-volumes
#   ACCESS_KEY=minioadmin
#   SECRET_KEY=minioadmin
#   REGION=us-east-1
#   USE_PATH_STYLE=1            # MinIO 必须；OBS 可选（推荐 1）
#   USE_CACHE_DIR=/data/cubelet/s3fs-cache  # s3fs 本地缓存目录（避开系统盘）
#
# 依赖（Cube 所有节点上均需装）：
#   s3fs-fuse — FUSE 挂载（create/destroy 也用它短挂 bucket 根）
#     Ubuntu:   apt install -y s3fs
#     TencentOS/CentOS 8: yum install -y s3fs-fuse
#   jq — 构造/解析 JSON 输出
#
# 挂载布局（每个 volume 一个独立 s3fs 进程）：
#   <volume-base-dir>/s3-<volume_id>/  →  <BUCKET>:/volumes/<volume_id>/
#   base 默认 /data/cube-shared/volume，Cubelet 通过 --volume-base-dir 传入。
#
# 并发锁：per-volume flock 位于 /run/cube-volume-s3/<volume_id>.lock

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CUBE_S3_CONFIG:-${SCRIPT_DIR}/volume-s3.conf}"
LOCK_DIR="/run/cube-volume-s3"
PASSWD_FILE="/etc/cube/.passwd-s3fs"

# Cubelet 默认的 volume base；attach 时会用 --volume-base-dir 覆盖。
VOLUME_BASE_DIR="/data/cube-shared/volume"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()      { echo "[cube-volume-s3] $*" >&2; }
die()      { log "ERROR: $*"; exit 1; }
ok_json()  { printf '{"error":""}\n'; }
err_json() { local msg; msg="$(printf '%s' "$1" | jq -Rn 'input')"; printf '{"error":%s}\n' "$msg"; }

volume_mountpoint() { echo "${VOLUME_BASE_DIR%/}/s3-$1"; }
volume_prefix()     { echo "volumes/$1"; }

load_config() {
    [[ -f "$CONFIG_FILE" ]] || die "config file not found: $CONFIG_FILE"
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
    [[ -n "${ENDPOINT:-}"    ]] || die "config: ENDPOINT is empty"
    [[ -n "${BUCKET:-}"      ]] || die "config: BUCKET is empty"
    [[ -n "${ACCESS_KEY:-}"  ]] || die "config: ACCESS_KEY is empty"
    [[ -n "${SECRET_KEY:-}"  ]] || die "config: SECRET_KEY is empty"
    REGION="${REGION:-us-east-1}"
    USE_PATH_STYLE="${USE_PATH_STYLE:-1}"
    USE_CACHE_DIR="${USE_CACHE_DIR:-}"
}

# s3fs 密钥文件格式：ACCESS_KEY:SECRET_KEY（跟 cosfs 不同，无 bucket 前缀）
ensure_passwd_file() {
    mkdir -p "$(dirname "$PASSWD_FILE")"
    local content="${ACCESS_KEY}:${SECRET_KEY}"
    if [[ "$(cat "$PASSWD_FILE" 2>/dev/null)" != "$content" ]]; then
        printf '%s\n' "$content" > "$PASSWD_FILE"
        chmod 600 "$PASSWD_FILE"
    fi
}

# per-volume flock，避免并发 attach/detach 打架
volume_lock_acquire() {
    local volume_id="$1"
    mkdir -p "$LOCK_DIR"
    exec 200>"${LOCK_DIR}/${volume_id}.lock"
    flock -x 200
    log "lock acquired for volume ${volume_id}"
}
volume_lock_release() { flock -u 200; }

# ---------------------------------------------------------------------------
# S3 backend ops — 复用 s3fs 挂 bucket 根，用 mkdir/rm -rf 建/删 volume 目录。
# 好处：绕开 curl --aws-sigv4（Ubuntu 22.04 curl 7.81 有已知的 SigV4 bug，跟
# MinIO 组合概率性签不上）。凭证只走一处（PASSWD_FILE），排错面小。
# 代价：多一次 fuse mount/unmount（~200ms），管理面不敏感。
# ---------------------------------------------------------------------------

# 短挂 bucket 根到临时目录（只在 create/destroy 用），把 <op> 作为 callback 跑
# 完立即卸载。op 拿到的第一个参数就是挂载点。
with_bucket_root_mounted() {
    local op="$1" volume_id="$2"
    local root
    root="$(mktemp -d /tmp/s3-root.XXXXXX)"
    log "s3fs: short-mount bucket root -> ${root}"

    local path_style_arg=""
    [[ "$USE_PATH_STYLE" == "1" ]] && path_style_arg="-ouse_path_request_style"

    local out rc=0
    set +e
    out="$(s3fs "${BUCKET}" "$root" \
        "-ourl=${ENDPOINT}" \
        ${path_style_arg} \
        "-opasswd_file=${PASSWD_FILE}" \
        "-oallow_other" 2>&1)"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]] || ! mountpoint -q "$root" 2>/dev/null; then
        log "ERROR: s3fs short-mount failed: ${out}"
        rmdir "$root" 2>/dev/null || true
        return 1
    fi

    # 跑用户回调，捕获退出码。回调用 "$1" = 挂载点，"$2" = volume_id。
    local cb_rc=0
    set +e
    "$op" "$root" "$volume_id"
    cb_rc=$?
    set -e

    # 无论成败先卸载
    fusermount -u "$root" 2>/dev/null || umount -l "$root" 2>/dev/null || true
    rmdir "$root" 2>/dev/null || true

    return "$cb_rc"
}

# create 回调：mkdir volumes/<id>；touch .keep 让目录空时也能被 ListObjects 看到
_create_cb() {
    local root="$1" volume_id="$2"
    local dir="${root}/volumes/${volume_id}"
    log "s3: mkdir -p ${dir} && touch .keep"
    mkdir -p "$dir" || return 1
    : > "${dir}/.keep" || return 1
    return 0
}

s3_create_dir() {
    local volume_id="$1"
    with_bucket_root_mounted _create_cb "$volume_id"
}

# destroy 回调：rm -rf volumes/<id>（找不到当成功，幂等）
_destroy_cb() {
    local root="$1" volume_id="$2"
    local dir="${root}/volumes/${volume_id}"
    if [[ ! -e "$dir" ]]; then
        log "s3: ${dir} not found, treating as success"
        return 0
    fi
    log "s3: rm -rf ${dir}"
    rm -rf "$dir" || return 1
    return 0
}

s3_remove_dir() {
    local volume_id="$1"
    with_bucket_root_mounted _destroy_cb "$volume_id"
}

# ---------------------------------------------------------------------------
# s3fs FUSE ops
# ---------------------------------------------------------------------------

s3fs_mount_volume() {
    local volume_id="$1"
    local mnt endpoint_arg path_style_arg cache_arg
    mnt="$(volume_mountpoint "$volume_id")"

    if mountpoint -q "$mnt" 2>/dev/null; then
        log "s3fs: volume ${volume_id} already mounted at ${mnt}"
        return 0
    fi

    mkdir -p "$mnt"
    endpoint_arg="-ourl=${ENDPOINT}"
    path_style_arg=""
    [[ "$USE_PATH_STYLE" == "1" ]] && path_style_arg="-ouse_path_request_style"

    cache_arg=""
    if [[ -n "$USE_CACHE_DIR" ]]; then
        mkdir -p "$USE_CACHE_DIR"
        cache_arg="-ouse_cache=${USE_CACHE_DIR}"
    fi

    log "s3fs: mounting ${BUCKET}:/$(volume_prefix "$volume_id") -> ${mnt}"
    local out rc=0
    set +e
    # s3fs 支持 BUCKET:/PATH 语法挂子目录。设置 allow_other 让沙箱容器能读写。
    # nonempty 允许挂载点非空（首次可能有 .keep 之类）；nomultipart 关掉分块上
    # 传避免 MinIO 兼容坑（生产 OBS 可以放开）。
    out="$(s3fs "${BUCKET}:/$(volume_prefix "$volume_id")" "$mnt" \
        "$endpoint_arg" \
        ${path_style_arg} \
        ${cache_arg} \
        "-opasswd_file=${PASSWD_FILE}" \
        "-oallow_other" \
        "-ononempty" \
        "-odbglevel=info" 2>&1)"
    rc=$?
    set -e

    if [[ "$rc" -ne 0 ]] || ! mountpoint -q "$mnt" 2>/dev/null; then
        log "ERROR: s3fs mount failed for ${volume_id}: ${out}"
        rmdir "$mnt" 2>/dev/null || true
        return 1
    fi
    log "s3fs: mounted ok"
}

s3fs_unmount_volume() {
    local mnt="$1"
    if mountpoint -q "$mnt" 2>/dev/null; then
        log "s3fs: unmounting ${mnt}"
        fusermount -u "$mnt" 2>/dev/null || umount -l "$mnt" 2>/dev/null || true
    else
        log "s3fs: ${mnt} not mounted, skipping"
    fi
    if [[ -d "$mnt" ]]; then
        rmdir "$mnt" 2>/dev/null && log "s3fs: removed ${mnt}" \
            || log "s3fs: could not remove ${mnt} (not empty?)"
    fi
}

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

# create: 在 bucket 里给这个 volume 建目录（写 volumes/<id>/.keep）
do_create() {
    local volume_id="$1" name="$2"
    log "create volumeID=${volume_id} name=${name}"
    load_config
    ensure_passwd_file
    s3_create_dir "$volume_id" || { err_json "s3 create failed for ${volume_id}"; exit 1; }
    # private_data 存 S3 key 前缀，Attach 时可以用（当前没依赖，为将来兼容）
    jq -cn --arg pd "$(volume_prefix "$volume_id")/" \
        '{ token: "", private_data: $pd, error: "" }'
}

# destroy: 递归删 bucket 里的 volumes/<id>/*
do_destroy() {
    local volume_id="$1"
    log "destroy volumeID=${volume_id}"
    load_config
    ensure_passwd_file
    s3_remove_dir "$volume_id" || { err_json "s3 destroy failed for ${volume_id}"; exit 1; }
    ok_json
}

# attach: 用 s3fs 挂 <bucket>:/volumes/<id> 到 <base>/s3-<id>
do_attach() {
    local sandbox_id="$1" volume_id="$2" ref_count="$3"
    local private_data="${4:-}"
    log "attach sandbox=${sandbox_id} volumeID=${volume_id} refcount_before=${ref_count}"

    load_config
    ensure_passwd_file

    volume_lock_acquire "$volume_id"
    trap 'volume_lock_release' EXIT

    s3fs_mount_volume "$volume_id" || {
        err_json "s3fs mount failed for volume ${volume_id}"
        exit 1
    }

    local mnt
    mnt="$(volume_mountpoint "$volume_id")"
    log "attach ready: host_path=${mnt}"

    jq -cn \
        --arg path "$mnt" \
        --arg vid  "$volume_id" \
        '{
            host_path: $path,
            metadata:  { mount_dir: $path, volume_id: $vid },
            error:     ""
        }'
}

# detach: 本 node 最后一个沙箱走时 umount；否则 no-op
do_detach() {
    local sandbox_id="$1" volume_id="$2" ref_count="$3"
    local metadata_json="$4"
    log "detach sandbox=${sandbox_id} volumeID=${volume_id} refcount_after=${ref_count}"

    if [[ "$ref_count" -gt 0 ]]; then
        log "skipping unmount: still in use (refcount_after=${ref_count})"
        ok_json
        return
    fi

    load_config
    volume_lock_acquire "$volume_id"
    trap 'volume_lock_release' EXIT

    local mnt
    mnt="$(printf '%s' "$metadata_json" | jq -r '.mount_dir // empty' 2>/dev/null)"
    [[ -n "$mnt" ]] || mnt="$(volume_mountpoint "$volume_id")"

    s3fs_unmount_volume "$mnt"
    log "detach done (S3 data preserved; delete volume to remove)"
    ok_json
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

OP=""
VOLUME_ID="" NAME=""
SANDBOX_ID="" NAMESPACE="" REF_COUNT="0"
METADATA="{}"
PRIVATE_DATA=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --op)              OP="$2";           shift 2 ;;
        --volume-id)       VOLUME_ID="$2";    shift 2 ;;
        --name)            NAME="$2";         shift 2 ;;
        --sandbox-id)      SANDBOX_ID="$2";   shift 2 ;;
        --namespace)       NAMESPACE="$2";    shift 2 ;;
        --ref-count)       REF_COUNT="$2";    shift 2 ;;
        --volume-base-dir) [[ -n "${2:-}" ]] && VOLUME_BASE_DIR="$2"; shift 2 ;;
        --private-data)    PRIVATE_DATA="$2"; shift 2 ;;
        --metadata)        METADATA="$2";     shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n "$OP" ]] || die "--op is required"

case "$OP" in
    create)  do_create  "$VOLUME_ID" "$NAME" ;;
    destroy) do_destroy "$VOLUME_ID" ;;
    attach)  do_attach  "$SANDBOX_ID" "$VOLUME_ID" "$REF_COUNT" "$PRIVATE_DATA" ;;
    detach)  do_detach  "$SANDBOX_ID" "$VOLUME_ID" "$REF_COUNT" "$METADATA" ;;
    *)       err_json "unknown op: ${OP}"; exit 1 ;;
esac
