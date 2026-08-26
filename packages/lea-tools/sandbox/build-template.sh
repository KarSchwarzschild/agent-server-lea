#!/bin/bash
# 在 CubeSandbox 主机 (10.192.241.137) 上执行
# 前提：主机上有 docker + cubemastercli

set -e

IMAGE_TAG="lea-sandbox-code:v1"
REGISTRY=""  # 如果有私有 registry 改这里，否则用本地镜像

echo "=== 1. Build custom image ==="
docker build -t $IMAGE_TAG -f Dockerfile.lea-sandbox .

echo "=== 2. Create template from image ==="
# 如果用远程 registry:
#   docker tag $IMAGE_TAG $REGISTRY/$IMAGE_TAG
#   docker push $REGISTRY/$IMAGE_TAG
#   cubemastercli tpl create-from-image --image $REGISTRY/$IMAGE_TAG --writable-layer-size 2G --expose-port 49999 --probe 49999

# 用本地镜像（CubeSandbox 支持 docker:// scheme 或本地 OCI tar）:
docker save $IMAGE_TAG -o /tmp/lea-sandbox.tar
cubemastercli tpl create-from-image --image oci:/tmp/lea-sandbox.tar --writable-layer-size 2G --expose-port 49999 --probe 49999

echo "=== 3. Watch build progress ==="
echo "Run: cubemastercli tpl watch --job-id <job_id>"
echo "When READY, note the template_id and update .env:"
echo "  CUBE_TEMPLATE=tpl-xxxxxx"
