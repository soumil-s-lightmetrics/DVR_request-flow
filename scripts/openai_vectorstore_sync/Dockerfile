FROM python:3.10.17-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends awscli gcc python3-dev gettext-base curl procps && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY openai_vectorstore_sync.py start_container.sh ./

ARG IMAGE_VERSION
RUN echo -n ${IMAGE_VERSION} > /etc/docker_application_image_version.txt

CMD ["/usr/src/app/start_container.sh"]