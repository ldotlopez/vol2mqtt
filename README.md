# vol2mqtt

Publish volume level from a ffmpeg source to MQTT

🎁 This is basically a big wrapper for:

```
ffmpeg \
  -i rtsp://your-ip-cam/ \
  -vn \
  -af astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level |
grep some-pattern |
mosquitto_pub
```

🐋 in a nice Docker package
