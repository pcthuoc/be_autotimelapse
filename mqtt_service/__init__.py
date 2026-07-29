"""MQTT service — cầu nối Django ↔ Mosquitto broker.

Topic (device_id = Camera.code):
    camera/{code}/data     Trạm → Server   telemetry (pin, solar, nhiệt, SIM dBm)
    camera/{code}/status   Trạm → Server   online/offline (LWT)
    camera/{code}/ack      Trạm → Server   phản hồi lệnh (applied settings, SIM info)
    camera/{code}/cmd      Server → Trạm   lệnh: set_settings / get_settings /
                                            get_sim_info / capture_now / set_interval
"""
