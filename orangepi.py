import smbus2
import time
import requests
import paho.mqtt.client as mqtt

# === Configuration ===
I2C_BUS = 0  # Use I2C bus 0
GY30_ADDR = 0x23
BMP280_ADDR = 0x76

# -------------------------------------------------
# 1) USE YOUR PRIVATE READ KEY HERE:
# -------------------------------------------------
READ_API_KEY_2792381 = "94U2IKT6YRPREMPN"  # <--- Replace with your channel's READ key
THRESHOLD_CHANNEL_ID = 2792381
THRESHOLD_FIELD = 8

# === ThingSpeak MQTT Configuration (for publishing lux) ===
CLIENT_ID  = "NBkUGh44NyIgAzIgCSEBFyk"
USERNAME   = "NBkUGh44NyIgAzIgCSEBFyk"
PASSWORD   = "IsVPqY83vLyeYpHSIbmABMYh"
CHANNEL_ID = "2792379"  # The channel to which we publish the lux reading
THINGSPEAK_MQTT_BROKER = "mqtt3.thingspeak.com"
THINGSPEAK_MQTT_PORT   = 1883  # or 8883 if using TLS/SSL

# ULN2003 Pin Definitions (Stepper Motor)
IN1 = 10  # GPIO 112, wPi 10, Physical Pin 18
IN2 = 11  # GPIO 229, wPi 11, Physical Pin 19
IN3 = 12  # GPIO 230, wPi 12, Physical Pin 21
IN4 = 14  # GPIO 228, wPi 14, Physical Pin 23

# RGB LED Pin Definitions
RED_PIN = 6     # GPIO 114, wPi 6, Physical Pin 12
GREEN_PIN = 13   # GPIO 111, wPi 9, Physical Pin 16
BLUE_PIN = 9   # GPIO 117, wPi 13, Physical Pin 22

# Temperature Thresholds for RGB LED
COLD_THRESHOLD = 18.0
WARM_THRESHOLD = 22.0

# Stepper Motor Step Sequence
step_sequence = [
    [1, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 1],
    [1, 0, 0, 1],
]

# ------------------------------------------------------------
# Functions
# ------------------------------------------------------------
def read_lux_goal_from_thingspeak():
    """
    Read the last posted value for field8 from channel 2792381
    via ThingSpeak's HTTP GET.

    This time we explicitly include ?api_key=YOUR_READ_API_KEY
    to handle private channels.
    """
    url = f"https://api.thingspeak.com/channels/{THRESHOLD_CHANNEL_ID}/fields/{THRESHOLD_FIELD}/last.txt?api_key={READ_API_KEY_2792381}"

    try:
        response = requests.get(url, timeout=5)  # 5s timeout
        response.raise_for_status()  # raise HTTPError if bad status
        text_value = response.text.strip()
        return float(text_value)
    except Exception as e:
        print(f"Error reading LUX_GOAL from ThingSpeak: {e}")
        # Fallback if there's an error
        return 100.0

def setup_gpio():
    import wiringpi as wp
    wp.wiringPiSetup()

    # Stepper Motor Pins
    wp.pinMode(IN1, wp.OUTPUT)
    wp.pinMode(IN2, wp.OUTPUT)
    wp.pinMode(IN3, wp.OUTPUT)
    wp.pinMode(IN4, wp.OUTPUT)
    wp.digitalWrite(IN1, wp.LOW)
    wp.digitalWrite(IN2, wp.LOW)
    wp.digitalWrite(IN3, wp.LOW)
    wp.digitalWrite(IN4, wp.LOW)

    # RGB LED Pins
    wp.pinMode(RED_PIN, wp.OUTPUT)
    wp.pinMode(GREEN_PIN, wp.OUTPUT)
    wp.pinMode(BLUE_PIN, wp.OUTPUT)

    # Default LED state
    wp.digitalWrite(RED_PIN, wp.LOW)
    wp.digitalWrite(GREEN_PIN, wp.LOW)
    wp.digitalWrite(BLUE_PIN, wp.HIGH)  # Start with blue

    return wp

def set_led_color_by_temp(wp, temperature):
    """
    If temperature < 20.0 => blue
    If 20.0 <= temperature <= 22.0 => green
    If temperature > 22.0 => red
    """
    if temperature < COLD_THRESHOLD:
        wp.digitalWrite(RED_PIN, wp.LOW)
        wp.digitalWrite(GREEN_PIN, wp.LOW)
        wp.digitalWrite(BLUE_PIN, wp.HIGH)
    elif COLD_THRESHOLD <= temperature <= WARM_THRESHOLD:
        wp.digitalWrite(RED_PIN, wp.LOW)
        wp.digitalWrite(GREEN_PIN, wp.HIGH)
        wp.digitalWrite(BLUE_PIN, wp.LOW)
    else:
        wp.digitalWrite(RED_PIN, wp.HIGH)
        wp.digitalWrite(GREEN_PIN, wp.LOW)
        wp.digitalWrite(BLUE_PIN, wp.LOW)

def step_motor(wp, direction, steps, delay=0.002):
    if direction == "open":
        sequence = step_sequence
    elif direction == "close":
        sequence = step_sequence[::-1]
    else:
        raise ValueError("Invalid direction. Use 'open' or 'close'.")

    for _ in range(steps):
        for step in sequence:
            wp.digitalWrite(IN1, step[0])
            wp.digitalWrite(IN2, step[1])
            wp.digitalWrite(IN3, step[2])
            wp.digitalWrite(IN4, step[3])
            time.sleep(delay)

    # Turn pins off after
    wp.digitalWrite(IN1, wp.LOW)
    wp.digitalWrite(IN2, wp.LOW)
    wp.digitalWrite(IN3, wp.LOW)
    wp.digitalWrite(IN4, wp.LOW)

def read_lux():
    """
    Read the lux from the BH1750 (GY-30) sensor over I2C
    """
    bus = smbus2.SMBus(I2C_BUS)
    # Continuously H-Resolution Mode (1 lx resolution, 120ms)
    bus.write_byte(GY30_ADDR, 0x10)
    time.sleep(0.2)
    data = bus.read_i2c_block_data(GY30_ADDR, 0x10, 2)
    raw_lux = (data[0] << 8) | data[1]
    return raw_lux / 1.2

def read_calibration_params(bus):
    calib = bus.read_i2c_block_data(BMP280_ADDR, 0x88, 24)
    dig_T1 = calib[1] << 8 | calib[0]
    dig_T2 = (calib[3] << 8 | calib[2]) - 65536 if (calib[3] & 0x80) else (calib[3] << 8 | calib[2])
    dig_T3 = (calib[5] << 8 | calib[4]) - 65536 if (calib[5] & 0x80) else (calib[5] << 8 | calib[4])
    return dig_T1, dig_T2, dig_T3

def compensate_temperature(raw_temp, dig_T1, dig_T2, dig_T3):
    var1 = (((raw_temp / 16384.0) - (dig_T1 / 1024.0)) * dig_T2)
    var2 = (((raw_temp / 131072.0) - (dig_T1 / 8192.0)) *
            ((raw_temp / 131072.0) - (dig_T1 / 8192.0)) * dig_T3)
    temp = (var1 + var2) / 5120.0
    return temp

def read_temperature(bus):
    # Forced mode, oversampling x1
    bus.write_byte_data(BMP280_ADDR, 0xF4, 0x2F)
    time.sleep(0.5)
    data = bus.read_i2c_block_data(BMP280_ADDR, 0xFA, 3)
    raw_temp = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
    dig_T1, dig_T2, dig_T3 = read_calibration_params(bus)
    temp = compensate_temperature(raw_temp, dig_T1, dig_T2, dig_T3)
    return temp

# MQTT Setup
def setup_mqtt_client():
    client = mqtt.Client(client_id=CLIENT_ID)
    client.username_pw_set(USERNAME, PASSWORD)
    client.connect(THINGSPEAK_MQTT_BROKER, THINGSPEAK_MQTT_PORT, 60)
    client.loop_start()
    return client

def publish_to_thingspeak_mqtt(client, lux):
    """
    Publish the lux value to channel 2792379, field8.
    """
    topic = f"channels/{CHANNEL_ID}/publish"
    payload = f"field8={lux}"
    result = client.publish(topic, payload)
    if result.rc == 0:
        print(f"Successfully published lux: {lux}")
    else:
        print(f"Failed to publish, result code: {result.rc}")

def main():
    wp = setup_gpio()
    bus = smbus2.SMBus(I2C_BUS)

    mqtt_client = setup_mqtt_client()
    state = None  # Track the state of the blinds

    last_update_time = time.time()
    update_interval = 20  # seconds

    try:
        while True:
            current_time = time.time()
            if current_time - last_update_time >= update_interval:
                last_update_time = current_time

                # 1) Fetch the LUX_GOAL from your private channel using the READ_API_KEY
                lux_goal = read_lux_goal_from_thingspeak()
                print(f"LUX_GOAL fetched from Channel {THRESHOLD_CHANNEL_ID}, field{THRESHOLD_FIELD}: {lux_goal}")

                # 2) Read sensors: GY30 for lux, BMP280 for temperature
                lux = read_lux()
                temp = read_temperature(bus)
                print(f"Lux: {lux:.1f}, Temp: {temp:.1f}C")

                # 3) Set LED color based on temperature
                set_led_color_by_temp(wp, temp)

                # 4) Publish lux value via MQTT to channel 2792379
                publish_to_thingspeak_mqtt(mqtt_client, lux)

                # 5) Control stepper motor based on lux vs. lux_goal
                if lux > lux_goal:
                    desired_state = "open"
                elif lux < lux_goal:
                    desired_state = "closed"
                else:
                    # If lux == lux_goal, keep current state
                    desired_state = state

                if desired_state != state:
                    if desired_state == "open":
                        print("Lux exceeds goal. Opening blinds...")
                        step_motor(wp, "open", 512)
                    elif desired_state == "closed":
                        print("Lux below goal. Closing blinds...")
                        step_motor(wp, "close", 512)
                    state = desired_state

            # Small delay to avoid busy waiting
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("Program stopped by user")
    finally:
        # Cleanup
        wp.digitalWrite(RED_PIN, wp.LOW)
        wp.digitalWrite(GREEN_PIN, wp.LOW)
        wp.digitalWrite(BLUE_PIN, wp.LOW)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == "__main__":
    main()
