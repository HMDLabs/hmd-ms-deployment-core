# syntax = docker/dockerfile:1.0-experimental

FROM ghcr.io/hmdlabs/hmd-ms-base:0.2.260

COPY meta-data/ /meta-data
COPY src/python/ /src/python
COPY ./src/docker/requirements.txt ${FUNCTION_DIR}

RUN --mount=type=secret,id=pipconfig,dst=/etc/pip.conf \
    pip install -r requirements.txt

ENV PYTHONPATH "/usr/local/lib/python3.11/site-packages"

# Licence texts travel with the image: BUSL 1.1 requires the licence to be
# conspicuously displayed on every copy, and an image is a copy. The
# orchestrator image (hmd-ms-deployment) is FROM this one and adds its own.
COPY LICENSE.txt LICENSE-Apache-2.0.txt NOTICE /licenses/hmd-ms-deployment-core/
LABEL org.opencontainers.image.licenses="BUSL-1.1 AND Apache-2.0"
