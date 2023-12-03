FROM alpine
MAINTAINER luis@cuarentaydos.com

RUN apk add --no-cache ffmpeg python3 py3-pip tini
RUN python3 -m pip install pipenv

RUN mkdir /app
COPY ./main.py ./Pipfile /app
RUN cd /app && pipenv install

COPY entrypoint.sh /
RUN chmod +x entrypoint.sh

ENTRYPOINT ["/sbin/tini", "-g", "--", "/entrypoint.sh"]
