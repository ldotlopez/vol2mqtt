FROM alpine
MAINTAINER luis@cuarentaydos.com

RUN apk add --no-cache ffmpeg python3 py3-pip tini
COPY requirements.txt /
RUN python3 -m pip install -r /requirements.txt && rm /requirements.txt
COPY main.py /
COPY entrypoint.sh /
RUN chmod +x entrypoint.sh

ENTRYPOINT ["/sbin/tini", "-g", "--", "/entrypoint.sh"]
