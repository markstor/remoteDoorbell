import logging
import json
import time
from gpiozero import Button, OutputDevice, DigitalOutputDevice
from signal import pause
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# Load MQTT setup from file
with open('mqtt_config.json') as f:
    credentials = json.load(f)
    MQTT_HOST = credentials['host']
    MQTT_PORT = int(credentials['port'])
    MQTT_USERNAME = credentials['username']
    MQTT_PASSWORD = credentials['password']

HA_DISCOVERY_PREFIX = "homeassistant"

class DoorBellDevice:
    DEVICE_UNIQUE_ID = "doorbell1234"
    ROOT_TOPIC = "home/doorbell"
    def __init__(self, client, components=None):
        if components is None:
            components = []
        self.components = components
        self.client = client
    
    def add_button(self, gpiopin, button_name = None):
        button = ButtonComponent(self, gpiopin, button_name)
        self.components.append(button)
    
    @property
    def discovery_topic(self):
        prefix = HA_DISCOVERY_PREFIX
        component = "device"
        object_id = self.DEVICE_UNIQUE_ID
        topic = f"{prefix}/{component}/{object_id}/config"
        return topic
    
    def discovery_payload(self):
        components_dict = {}
        for cmp in self.components:
            components_dict.update(cmp.component_discovery_payload())

        discovery_payload = {
        "dev": {
            "ids": self.DEVICE_UNIQUE_ID,  # Unique ID for the device
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
        "state_topic": f"{self.ROOT_TOPIC}/state",
        "availability_topic": f"{self.ROOT_TOPIC}/availability",
        "qos": 1
        }
        return discovery_payload

    def publish_discovery_payload(self):
        discovery_payload = self.discovery_payload()
        self.client.publish(self.discovery_topic, json.dumps(discovery_payload), qos=1, retain=True)
        logging.debug(f"Payload: {discovery_payload}")
        logging.info(f"Discovery payload published in topic {self.discovery_topic}")
    
    def publish_availability(self, payload = "online"):
        availability_topic = f"{self.ROOT_TOPIC}/availability"
        self.client.publish(availability_topic, payload, qos=1, retain=True)
        logging.debug(f"Availability message published in topic {availability_topic}")
        for component in self.components:
            self.client.publish(f"{component.root_topic}/availability", payload, qos=1, retain=True)
            logging.debug(f"Availability message published in topic {component.root_topic}/availability")
        logging.info(f"Availability status published: {payload}")
    
    def setup(self):        
        def on_connect(client, userdata, flags, rc):
            logging.info(f"Connected with result code {rc}")
            # Subscribe to all component topics
            for cmp in self.components:
                logger.info(f"Subscribing to topic {cmp.root_topic}")
                client.subscribe(cmp.root_topic)
            # Publish discovery payload
            self.publish_discovery_payload()
            self.publish_availability("online")

        def on_message(client, userdata, msg):
            logging.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")
            for cmp in self.components:
                if msg.topic == cmp.root_topic:
                    #button.activate_button()
                    logging.info(f"Button {cmp.object_id} activated")

        self.client.on_connect = on_connect
        self.client.on_message = on_message


    def shutdown(self):
        self.publish_availability("offline")
        del self.components
        self.components = []
        

class ButtonComponent:
    def __init__(self, parent_device: DoorBellDevice, gpio_pin, button_name=None, active_time=0.2):
        self.active_time = active_time
        self.gpio_pin = gpio_pin
        self.button_name = button_name
        self.parent_device = parent_device
        if self.button_name is None:
            self.button_name = f"Button{self.gpio_pin}"
        self.configure_input_button()

    def configure_input_button(self):
        self.input_button = Button(self.gpio_pin, pull_up=False)
        self.input_button.when_pressed = self.on_button_press

    def on_button_press(self):
        logging.info(f"Button on pin {self.input_button.pin.number} was pressed!")
        
        # Log button press via MQTT
        message = {"button": self.input_button.pin.number, "action": "pressed"}
        self.parent_device.client.publish(f"{self.root_topic}/command", json.dumps(message), qos=1)

    def activate_button(self):
        logging.info(f"Activating button on pin {self.output_control.pin.number}")
        
        self.input_button.close()
        with DigitalOutputDevice(self.gpio_pin, active_high=False) as output_control:
            output_control.on()
            time.sleep(self.active_time)
            output_control.off()
        self.configure_input_button()

    @property
    def object_id(self):
        # sanitize button name
        return f"{self.button_name.lower().replace(' ', '')}"

    @property
    def root_topic(self):
        topic = f"home/doorbell/{self.object_id}"
        return topic

    def component_discovery_payload(self):
        return {self.object_id:{
            "p": "button",
            "name": self.button_name,
            "state_topic": f"{self.root_topic}/state",
            "availability_topic": f"{self.root_topic}/availability",
            "command_topic": f"{self.root_topic}/command",
            "unique_id": f"{self.parent_device.DEVICE_UNIQUE_ID}_{self.object_id}",
        }}

def main():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    
    client = mqtt.Client()
    
    doorbell = DoorBellDevice(client)
    doorbell.add_button(14, "Door Button")
    doorbell.add_button(15, "Video Button")
    doorbell.setup()

    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT, 60)

    # Start MQTT loop in the background
    client.loop_start()

    try:
        # Wait for events indefinitely
        pause()

    except KeyboardInterrupt:
        doorbell.shutdown()
        logging.info("Exiting script...")

    client.loop_stop()
    client.disconnect()

if __name__ == "__main__":
    main()