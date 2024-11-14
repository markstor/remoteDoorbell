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
    def __init__(self, parent_device, name):
        self.parent_device = parent_device
        self.name = name

    @property
    def object_id(self):
        # sanitize name
        return f"{self.name.lower().replace(' ', '')}"
    @property
    def client(self):
        return self.parent_device.client

    @property
    def root_topic(self):
        topic = f"{self.parent_device.ROOT_TOPIC}/{self.object_id}"
        return topic

    def component_discovery_payload(self):
        return {self.object_id:{
            "p": self.PLATFORM,
            "name": self.name,
            "state_topic": f"{self.root_topic}/state",
            "availability_topic": f"{self.root_topic}/availability",
            "command_topic": f"{self.root_topic}/command",
            "unique_id": f"{self.parent_device.DEVICE_UNIQUE_ID}_{self.object_id}",
        }}
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
        self.client.publish(f"{self.root_topic}/command", "PRESS", qos=1)

    def activate_button(self):
        logging.info(f"Activating button on pin {self.output_control.pin.number}")
        
        self.input_button.close()
        with DigitalOutputDevice(self.gpio_pin, active_high=False) as output_control:
            output_control.on()
            time.sleep(self.active_time)
            output_control.off()
        self.configure_input_button()

class DoorSensor(Component):
    PLATFORM = "binary_sensor"
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
        self.input = DigitalInputDevice(self.gpio_pin)
        self.input.when_activated = self.on_activation
        self.input.when_deactivated = self.on_deactivation
    
    def on_activation(self):
        logging.info(f"Pickup switch activated!")
        self.client.publish(f"{self.root_topic}/state", "ON", qos=1)
    
    def on_deactivation(self):
        logging.info(f"Pickup switch deactivated")
        self.client.publish(f"{self.root_topic}/state", "OFF", qos=1)

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
        self.add_button(14, "Door Button")
        self.add_button(15, "Video Button")
        self.components.append(DoorSensor(self, "Door Sensor", 18))
        self.components.append(VideoSensor(self, "Video Sensor", 23))
        self.pickup_switch = PickUpSwitch(self, "Pickup Switch", 24)
        self.components.append(self.pickup_switch)

        def on_connect(client, userdata, flags, rc):
            logging.info(f"Connected with result code {rc}")
            # Subscribe to all sub topics
            topic_pattern = f"{self.ROOT_TOPIC}/#"
            client.subscribe(topic_pattern)
            logger.info(f"Subscribed to {topic_pattern}")
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
    
    def start_video_stream(self):
        logging.info("Starting video stream...")
        # stream video using go2rtc
        """
        configured with following yaml:
        streams:
            stream: ffmpeg:device?video=0#video=h264
            play_pcma: exec:ffplay -fflags nobuffer -f alaw -ar 8000 -i -#backchannel=1
        """
        # start video stream
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