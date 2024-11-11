import os
import json
import time
from gpiozero import Button, OutputDevice, DigitalOutputDevice
from signal import pause
import paho.mqtt.client as mqtt

# MQTT Setup
MQTT_BROKER = "homeassistant.casa.casalprim.xyz"


MQTT_DISCOVERY_PREFIX = "homeassistant"
MQTT_UNIQUE_ID = "doorbell1992"

class ButtonController:
    def __init__(self, gpio_pin, button_name=None, active_time=0.2):
        self.active_time = active_time
        self.gpio_pin = gpio_pin
        self.button_name = button_name
        if self.button_name is None:
            self.button_name = f"Button{self.gpio_pin}"
        self.configure_input_button()

    def configure_input_button(self):
        self.input_button = Button(self.gpio_pin, pull_up=False)
        self.input_button.when_pressed = self.on_button_press

    def on_button_press(self):
        print(f"Button on pin {self.input_button.pin.number} was pressed!")
        
        # Log button press via MQTT
        message = {"button": self.input_button.pin.number, "action": "pressed"}
        client.publish(self.topic, json.dumps(message), qos=1)

    def activate_button(self):
        print(f"Activating button on pin {self.output_control.pin.number}")
        
        self.input_button.close()
        with DigitalOutputDevice(self.gpio_pin, active_high=False) as output_control:
            output_control.on()
            time.sleep(self.active_time)
            output_control.off()
        self.configure_input_button()

    @property
    def object_id(self):
        # sanitize button name
        return f"{self.button_name.lower().replace(' ', '_')}"

    @property
    def topic(self):
        topic = f"home/doorbell/{self.object_id}"
        return topic

    def component_discovery_payload(self):
        return {self.object_id:{
            "p": "button",
            "state_topic": f"{self.topic}/state",
            "availability_topic": f"{self.topic}/availability",
            "device_class": "motion",
            "unique_id": f"{MQTT_UNIQUE_ID}_{self.object_id}",
        }}
    
    

def discovery_topic():
    prefix = MQTT_DISCOVERY_PREFIX
    component = "device"
    object_id = MQTT_UNIQUE_ID
    topic = f"{prefix}/{component}/{object_id}"
    return topic

def generate_discovery_payload(components_dict):
    discovery_payload = {
    "dev": {
        "ids": MQTT_UNIQUE_ID,  # Unique ID for the device
        "name": "Interfono",
        "mf": "PRIM, S.A.",
        "mdl": "UltraGuard",
        "sw": "1.0",
        "sn": "1234567890",
        "hw": "v1"
    },
    "o": {
        "name": "PRIM System",
        "sw": "0.1",
        "url": "https://blog.casalprim.xyz"
    },
    "cmps": components_dict,
    "state_topic": "home/doorbell/state",
    "qos": 1
    }
    return discovery_payload

def publish_discovery_payload(buttons):
    components = {}
    for button in buttons:
        components.update(button.component_discovery_payload())
    discovery_payload = generate_discovery_payload(components)
    prefix = MQTT_DISCOVERY_PREFIX
    component = "device"
    object_id = MQTT_UNIQUE_ID
    topic = f"{prefix}/{component}/{object_id}"
    client.publish(topic, json.dumps(discovery_payload), qos=1, retain=True)

buttons = [
    ButtonController(14, "doorButton"),
    ButtonController(15, "videoButton")
]

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    # Subscribe to all button topics
    for button in buttons:
        client.subscribe(button.topic)
    # Publish discovery payload
    publish_discovery_payload(buttons)
    print("Discovery payload published")

def on_message(client, userdata, msg):
    print(f"Received message on topic {msg.topic}: {msg.payload.decode()}")
    for button in buttons:
        if msg.topic == button.topic:
            #button.activate_button()
            print(button.name, "detected")

# Setup MQTT client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.username_pw_set("your_username", "your_password")

client.connect(MQTT_BROKER, 1883, 60)

# Start MQTT loop in the background
client.loop_start()

try:
    # Wait for events indefinitely
    pause()

except KeyboardInterrupt:
    del buttons
    print("Exiting script...")

client.loop_stop()
client.disconnect()
