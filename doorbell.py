import logging
import json
import time
import datetime
from gpiozero import Button, DigitalOutputDevice, DigitalInputDevice
import signal
import subprocess
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# GPIO pins used
DOOR_BUTTON_GPIO = 14
VIDEO_BUTTON_GPIO = 15
VIDEO_SENSOR_GPIO = 4

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

    @property
    def state_topic(self): 
        return f"{self.root_topic}/state"
    
    @property
    def command_topic(self):
        return f"{self.root_topic}/command"

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

    def subscribe(self):
        for topic in self.TOPIC_HANDLING:
            self.client.subscribe(f"{self.root_topic}/{topic}")
            logging.info(f"Subscribed to {self.root_topic}/{topic}")

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
        logging.info(f"Button {self.name} on pin {self.gpio_pin} was pressed!")
        self.client.publish(f"{self.root_topic}/state", "PRESS", qos=1)

    def handle_command(self, payload):
        if payload == "PRESS":
            logging.info(f"Activating button on pin {self.gpio_pin}")            
            self.input_button.close()
            with DigitalOutputDevice(self.gpio_pin, active_high=False) as output_control:
                output_control.on()
                time.sleep(self.active_time)
                output_control.off()
            self.configure_input_button()
        else:
            logging.warning(f"Unknown command received: {payload}")

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

class Camera(Component):
    PLATFORM = "camera"
    TOPIC_HANDLING = []
    SUBTOPICS=["availability","json_attributes"]
        
    def subtopics_dict(self):
        sd = super().subtopics_dict()
        sd["topic"]=f"{self.root_topic}/data"
        return sd

    def publish_frame(self):
        try:
            # Use ffmpeg to capture a single frame from the RTSP stream
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",        # Remove initial welcome log
                    # "-v","error",          # Reduce verbosity
                    "-i", "/dev/video0",   # Input RTSP stream      
                    "-f", "v4l2",    # Use Video4Linux2 as input format
                    "-i", "/dev/video0",  # Input device
                    "-r", "10",           # Set frame rate (irrelevant for a single frame but needed by some devices)
                    "-pix_fmt", "yuv420p", # Set pixel format
                    "-c:v", "h264_v4l2m2m",      # Encode as MJPEG (or another format if needed)
                    "-frames:v", "1",     # Capture only one frame
                    "-vframes", "1",       # Capture a single frame
                    "-f", "image2pipe",    # Output format as raw image data
                    "-vcodec", "mjpeg",    # Encode as JPEG (adjust as needed)
                    "-"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            payload = result.stdout  # Binary data of the frame
        except subprocess.CalledProcessError as e:
            print(f"Error capturing frame: {e.stderr.decode()}")
            return
        
        # save in filesystem for debugging purposes
        with open(f"snapshot.jpg","wb") as f:
            f.write(payload)
        attributes_dict={"published_at": datetime.datetime.today().isoformat()}
        self.client.publish(self.subtopics_dict()["topic"], payload, qos=1)
        self.client.publish(self.subtopics_dict()["json_attributes_topic"], json.dumps(attributes_dict) , qos=1)
        logger.info("Published snapshot")

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
        self.add_button(DOOR_BUTTON_GPIO, "Door Button")
        self.add_button(VIDEO_BUTTON_GPIO, "Video Button")
        self.components.append(VideoSensor(self, "Video Sensor", VIDEO_SENSOR_GPIO))
        self.camera = Camera(self, "Doorbell")
        self.components.append(self.camera)
        def on_connect(client, userdata, flags, rc):
            logging.info(f"Connected with result code {rc}")
            for cmp in self.components:
                cmp.subscribe()
            self.publish_discovery_payload()
            self.publish_availability("online")

        def on_message(client, userdata, msg):
            logging.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")
            for cmp in self.components:
                if msg.topic.startswith(cmp.root_topic):
                    cmp.handle_message(msg)

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)


    def shutdown(self, signum=None, frame=None):
        self.publish_availability("offline")
        del self.components
        self.components = []
        self.stop_go2rtc()
        self.client.loop_stop()
        self.client.disconnect()
    
    def start_go2rtc(self):
        logging.info("Starting video stream...")
        subprocess.Popen(["./go2rtc", "-c", "go2rtc.yaml"])
    
    def stop_go2rtc(self):
        # Stop RTSP stream video from USB device
        logging.info("Stopping video stream...")
        # stop video stream
        subprocess.Popen(["killall", "go2rtc"])
        subprocess.Popen(["killall", "ffmpeg"])

    def start_video_stream(self):
        time.sleep(1)
        self.camera.publish_frame()
        self.start_go2rtc()
    def stop_video_stream(self):
        self.stop_go2rtc()

def main():
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')
    
    client = mqtt.Client()
    
    doorbell = DoorBellDevice(client)
    doorbell.setup()

    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT, 60)

    # Start MQTT loop in the background
    client.loop_start()

    try:
        # Wait for events indefinitely
        signal.pause()
    except KeyboardInterrupt:
        logging.info("CTRL+c Manually exiting script...")
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
