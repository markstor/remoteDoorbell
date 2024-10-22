import os
from gpiozero import Button, OutputDevice
from signal import pause
import paho.mqtt.client as mqtt

# GPIO pins for microphone and speaker control
mic_control_pin = 23  # GPIO to control microphone (or ADC power)
speaker_control_pin = 24  # GPIO to control speaker (or DAC/amplifier power)

# Setup GPIO
mic_control = OutputDevice(mic_control_pin)
speaker_control = OutputDevice(speaker_control_pin)

# MQTT Setup
MQTT_BROKER = "your_mqtt_broker_ip"
MQTT_TOPIC_MIC = "home/doorbell/request_mic"
MQTT_TOPIC_SPEAKER = "home/doorbell/request_speaker"

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe([(MQTT_TOPIC_MIC, 0), (MQTT_TOPIC_SPEAKER, 0)])

def on_message(client, userdata, msg):
    if msg.topic == MQTT_TOPIC_MIC:
        if msg.payload.decode() == 'on':
            activate_microphone()
        elif msg.payload.decode() == 'off':
            deactivate_microphone()
    elif msg.topic == MQTT_TOPIC_SPEAKER:
        if msg.payload.decode() == 'on':
            activate_speaker()
        elif msg.payload.decode() == 'off':
            deactivate_speaker()

# Functions to control the microphone and speaker
def activate_microphone():
    print("Activating microphone...")
    mic_control.on()  # Power on microphone/ADC
    # Setup go2rtc to stream audio from microphone
    os.system("go2rtc -config mic_stream.yaml &")

def deactivate_microphone():
    print("Deactivating microphone...")
    mic_control.off()  # Power off microphone/ADC
    os.system("pkill go2rtc")

def activate_speaker():
    print("Activating speaker...")
    speaker_control.on()  # Power on speaker/DAC
    # Setup go2rtc to stream audio to speaker
    os.system("go2rtc -config speaker_stream.yaml &")

def deactivate_speaker():
    print("Deactivating speaker...")
    speaker_control.off()  # Power off speaker/DAC
    os.system("pkill go2rtc")

# Setup MQTT client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, 1883, 60)

# Start MQTT loop in the background
client.loop_start()

try:
    # Wait for events indefinitely
    pause()

except KeyboardInterrupt:
    deactivate_microphone()
    deactivate_speaker()
    print("Exiting script...")

client.loop_stop()
client.disconnect()
