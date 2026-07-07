FROM python:3.10-slim-bullseye AS runstage

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

ENV FILEBEAT_VERSION=7.9.0
ENV FILEBEAT_URL=https://artifacts.elastic.co/downloads/beats/filebeat/filebeat-oss-${FILEBEAT_VERSION}-linux-x86_64.tar.gz

RUN apt update
RUN apt install -y awscli gcc python3-dev gettext-base curl procps
RUN curl -L $FILEBEAT_URL | tar -xz -C /tmp/ && \
    mv /tmp/filebeat-${FILEBEAT_VERSION}-linux-x86_64 /etc/filebeat && \
    cp /etc/filebeat/filebeat /usr/bin/ && \
    rm -rf /tmp/filebeat-${FILEBEAT_VERSION}-linux-x86_64

RUN mkdir -p /usr/src/app/logs && \
    chmod -R 777 /usr/src/app/logs

COPY requirements-app.txt ./
RUN pip install -r requirements-app.txt
COPY requirements-app.txt ./
RUN pip install -r requirements-app.txt

ADD utils ./utils
ADD tools ./tools
ADD DVR_code ./DVR_code
COPY main.py main-DVR.py config_log.py load_bulk_docs.py load_bulk_docs_categorised.py assistant_rag.py logger.py start_container.sh ./

COPY rag_utils/pinecone_openai_rag.py ./rag_utils/
COPY files/filebeat/filebeat.yml.template /etc/filebeat/

ARG IMAGE_VERSION
RUN echo -n ${IMAGE_VERSION} > /etc/docker_application_image_version.txt

CMD ["/usr/src/app/start_container.sh"]
