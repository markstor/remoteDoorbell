import logging
import json
import time
from gpiozero import Button, DigitalOutputDevice, DigitalInputDevice
from signal import pause
import subprocess
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
       
class Component:
    PLATFORM = "component"
    SUBTOPICS = ["state", "availability", "command"]
    TOPIC_HANDLING = ["state", "command"]
    def __init__(self, parent_device, name):
        self.parent_device = parent_device
        self.name = name

    @property
    def object_id(self):
        # sanitize name
        return f"{self.name.lower().replace(' ', '_')}"
    @property
    def client(self):
        return self.parent_device.client

    @property
    def root_topic(self):
        topic = f"{self.parent_device.ROOT_TOPIC}/{self.object_id}"
        return topic

    def subtopics_dict(self):
        return {f"{subtopic}_topic": f"{self.root_topic}/{subtopic}" for subtopic in self.SUBTOPICS}

    def component_discovery_payload(self):
        return {self.object_id:{
            "p": self.PLATFORM,
            "name": self.name,
            **self.subtopics_dict(),
            "object_id": self.object_id,
            "unique_id": f"{self.parent_device.DEVICE_UNIQUE_ID}_{self.object_id}",
        }}

    def handle_message(self, msg):
        topic = msg.topic
        payload = msg.payload.decode()
        if topic == self.state_topic:
            self.handle_state(payload)
        elif topic == self.command_topic:
            self.handle_command(payload)
    
    def handle_state(self, payload):
        pass
    
    def handle_command(self, payload):
        pass

    def topics_subscribe(self):
        for topic in self.TOPIC_HANDLING:
            self.client.subscribe(f"{self.root_topic}/{topic}")

class ButtonComponent(Component):
    PLATFORM = "button"
    def __init__(self, parent_device, gpio_pin, name=None, active_time=0.2):
        if name is None:
            name = f"Button{self.gpio_pin}"
        super().__init__(parent_device, name)
        self.active_time = active_time
        self.gpio_pin = gpio_pin
        self.configure_input_button()

    def configure_input_button(self):
        self.input_button = Button(self.gpio_pin, pull_up=False)
        self.input_button.when_pressed = self.on_button_press

    def on_button_press(self):
        logging.info(f"Button {self.name} on pin {self.input_button.pin.number} was pressed!")
        self.client.publish(f"{self.root_topic}/state", "PRESS", qos=1)

    def handle_command(self, payload):
        if payload == "PRESS":
            logging.info(f"Activating button on pin {self.output_control.pin.number}")
            
            self.input_button.close()
            with DigitalOutputDevice(self.gpio_pin, active_high=False) as output_control:
                output_control.on()
                time.sleep(self.active_time)
                output_control.off()
            self.configure_input_button()
        else:
            logging.warning(f"Unknown command received: {payload}")

class DoorSensor(Component):
    PLATFORM = "binary_sensor"
    TOPIC_HANDLING = []
    def __init__(self, parent_device, name, gpio_pin):
        super().__init__(parent_device, name)
        self.gpio_pin = gpio_pin
        self.input = DigitalInputDevice(self.gpio_pin)
        self.input.when_activated = self.on_activation
        self.input.when_deactivated = self.on_deactivation
    
    def on_activation(self):
        logging.info(f"Detected someone at the door!")
        self.client.publish(f"{self.root_topic}/state", "ON", qos=1)
    
    def on_deactivation(self):
        logging.info(f"No one at the door")
        self.client.publish(f"{self.root_topic}/state", "OFF", qos=1)    

class VideoSensor(Component):
    PLATFORM = "binary_sensor"
    TOPIC_HANDLING = []
    def __init__(self, parent_device, name, gpio_pin):
        super().__init__(parent_device, name)
        self.gpio_pin = gpio_pin
        self.input = DigitalInputDevice(self.gpio_pin)
        self.input.when_activated = self.on_activation
        self.input.when_deactivated = self.on_deactivation
    
    def on_activation(self):
        logging.info(f"Video input available!")
        self.client.publish(f"{self.root_topic}/state", "ON", qos=1)
        self.parent_device.start_video_stream()
    
    def on_deactivation(self):
        logging.info(f"Video input not available")
        self.client.publish(f"{self.root_topic}/state", "OFF", qos=1)
        self.parent_device.stop_video_stream()    

class PickUpSwitch(Component):
    PLATFORM = "switch"
    def __init__(self, parent_device, name, gpio_pin):
        super().__init__(parent_device, name)
        self.gpio_pin = gpio_pin
    
    def configure_input_pin(self):
        self.input = DigitalInputDevice(self.gpio_pin)
        self.input.when_activated = self.on_activation
        self.input.when_deactivated = self.on_deactivation
    
    def on_activation(self):
        logging.info(f"Pickup switch activated!")
        self.client.publish(f"{self.root_topic}/state", "ON", qos=1)
    
    def on_deactivation(self):
        logging.info(f"Pickup switch deactivated")
        self.client.publish(f"{self.root_topic}/state", "OFF", qos=1)
    
    def handle_command(self, payload):
        if payload == "ON":
            logging.info(f"Activating pickup switch")
            self.input.close()
            with DigitalOutputDevice(self.gpio_pin, active_high=True) as output_control:
                output_control.on()
        elif payload == "OFF":
            logging.info(f"Deactivating pickup switch")
            # we set it on High-Z, to not battle the physical switch
            self.input.close()
            self.configure_input_pin()
        else:
            logging.warning(f"Unknown command received: {payload}")

class DoorBellDevice:
    DEVICE_UNIQUE_ID = "doorbell1234"
    ROOT_TOPIC = "home/doorbell"
    def __init__(self, client, components=None):
        if components is None:
            components = []
        self.components = components
        self.client = client
        self.pickup_switch = None
    
    def add_button(self, gpiopin, button_name = None):
        button = ButtonComponent(self, gpiopin, button_name)
        self.components.append(button)
        return button
    
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
            "ids": self.DEVICE_UNIQUE_ID,
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
        # "state_topic": f"{self.ROOT_TOPIC}/state",
        # "availability_topic": f"{self.ROOT_TOPIC}/availability",
        "qos": 1
        }
        return discovery_payload

    def publish_discovery_payload(self):
        discovery_payload = self.discovery_payload()
        self.client.publish(self.discovery_topic, json.dumps(discovery_payload), qos=1, retain=True)
        logging.debug(f"Payload: {discovery_payload}")
        logging.info(f"Discovery payload published in topic {self.discovery_topic}")
    
    def remove_discovery_payload(self):
        self.client.publish(self.discovery_topic, "", qos=1, retain=True)
        logging.info(f"Discovery payload removed from topic {self.discovery_topic}")
    
    def publish_availability(self, payload = "online"):
        availability_topic = f"{self.ROOT_TOPIC}/availability"
        self.client.publish(availability_topic, payload, qos=1, retain=True)
        logging.debug(f"Availability message published in topic {availability_topic}")
        for component in self.components:
            availability_topic = component.subtopics_dict()["availability_topic"]
            self.client.publish(availability_topic, payload, qos=1, retain=True)
            logging.debug(f"Availability message published in topic {availability_topic}")
        logging.info(f"Availability status published: {payload}")
    
    def setup(self):
        self.add_button(14, "Door Button")
        self.add_button(15, "Video Button")
        self.components.append(DoorSensor(self, "Door Sensor", 2))
        self.components.append(VideoSensor(self, "Video Sensor", 4))
        # self.pickup_switch = PickUpSwitch(self, "Pickup Switch", 24)
        # self.components.append(self.pickup_switch)

        def on_connect(client, userdata, flags, rc):
            logging.info(f"Connected with result code {rc}")
            for cmp in self.components:
                for topic_pattern in cmp.topics_subscribe():
                    client.subscribe(topic_pattern)
                    logging.info(f"Subscribed to {topic_pattern}")
            self.publish_discovery_payload()
            self.publish_availability("online")

        def on_message(client, userdata, msg):
            logging.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")
            for cmp in self.components:
                if msg.topic.startswith(cmp.root_topic):
                    cmp.handle_message(msg)

        self.client.on_connect = on_connect
        self.client.on_message = on_message


    def shutdown(self):
        self.publish_availability("offline")
        del self.components
        self.components = []
    
    def start_video_stream(self):
        logging.info("Starting video stream...")
        subprocess.Popen(["go2rtc", "-c", "go2rtc.yaml"])
    
    def stop_video_stream(self):
        # Stop RTSP stream video from USB device
        logging.info("Stopping video stream...")
        # stop video stream
        subprocess.Popen(["killall", "go2rtc"])




def main():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    
    client = mqtt.Client()
    
    doorbell = DoorBellDevice(client)
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