import time
import board
import busio
import adafruit_bmp280
import wifi
import socketpool
import rtc
import adafruit_ntp
import adafruit_pcd8544

from digitalio import DigitalInOut, Direction, Pull
import adafruit_minimqtt.adafruit_minimqtt as MQTT

# --------------------------------------------------------------------------------
#                      Wi-Fi + NTP + MQTT Configuration
# --------------------------------------------------------------------------------
SSID = "IoT"
PASSWORD = "IoTPassword"

# Timezone (Belgium UTC+1 normally, +2 if DST)
UTC_OFFSET = 1

# ThingSpeak MQTT (for field 8)
MQTT_BROKER = "mqtt3.thingspeak.com"
MQTT_PORT = 1883
USERNAME = "IBMdDQkiOC0nKyYyPCUHEwc"
CLIENT_ID = "IBMdDQkiOC0nKyYyPCUHEwc"
PASSWORD_MQTT = "X2KKO3Gx0P3is62dukeRX3ll"
CHANNEL_ID = "2792381"
WRITE_API_KEY_FIELD8 = "2HBKEUX8ENIXKAGX"

# The MQTT topic for publishing to field 8
MQTT_TOPIC = f"channels/{CHANNEL_ID}/publish"

# --------------------------------------------------------------------------------
#                            I2C and BMP280 Setup
# --------------------------------------------------------------------------------
i2c = busio.I2C(scl=board.GP17, sda=board.GP16)
bmp = adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x76)

# --------------------------------------------------------------------------------
#                         LEDs on GP13 & GP15
# --------------------------------------------------------------------------------
# GP13 => White LED for the original button
led_button = DigitalInOut(board.GP13)
led_button.direction = Direction.OUTPUT

# GP15 => Blue LED based on time (07:00 - 20:00)
led_time = DigitalInOut(board.GP15)
led_time.direction = Direction.OUTPUT

# --------------------------------------------------------------------------------
#                             Original Button on GP14
# --------------------------------------------------------------------------------
# Toggles LED on GP13 when pressed
button = DigitalInOut(board.GP14)
button.direction = Direction.INPUT
button.pull = Pull.DOWN

# --------------------------------------------------------------------------------
#                        NEW Button on GP12 for MQTT field8
# --------------------------------------------------------------------------------
button2 = DigitalInOut(board.GP12)
button2.direction = Direction.INPUT
button2.pull = Pull.DOWN

# --------------------------------------------------------------------------------
#                           Nokia 5110 LCD (PCD8544)
# --------------------------------------------------------------------------------
spi = busio.SPI(clock=board.GP6, MOSI=board.GP7)
dc = DigitalInOut(board.GP4)
cs = DigitalInOut(board.GP5)
rst = DigitalInOut(board.GP8)
lcd = adafruit_pcd8544.PCD8544(spi, dc, cs, rst)
lcd.contrast = 60  # Adjust as needed
lcd.fill(0)
lcd.show()

# --------------------------------------------------------------------------------
#                           Connect to Wi-Fi
# --------------------------------------------------------------------------------
def connect_wifi():
    try:
        print(f"Connecting to Wi-Fi: {SSID}")
        wifi.radio.connect(SSID, PASSWORD)
        print("Connected to Wi-Fi:", wifi.radio.ipv4_address)
    except Exception as e:
        print("Failed to connect to Wi-Fi:", e)
        while True:
            pass  # Stop execution if Wi-Fi fails

# --------------------------------------------------------------------------------
#                           Synchronize with NTP
# --------------------------------------------------------------------------------
def sync_time(pool):
    try:
        ntp = adafruit_ntp.NTP(pool, tz_offset=UTC_OFFSET)
        rtc.RTC().datetime = ntp.datetime
        print("Time synchronized:", time.localtime())
    except Exception as e:
        print("Failed to synchronize time:", e)

# --------------------------------------------------------------------------------
#                         Update Nokia 5110 LCD
# --------------------------------------------------------------------------------
def update_lcd(temperature, pressure, hour, minute):
    lcd.fill(0)  # Clear the display
    lcd.text(f"Temp: {temperature:.2f}C", 0, 0, 1)      # Line 1
    lcd.text(f"Press:{pressure:.2f}hPa", 0, 10, 1)      # Line 2
    lcd.text(f"Time:{hour:02}:{minute:02}", 0, 20, 1)   # Line 3
    lcd.show()

# --------------------------------------------------------------------------------
#                           MQTT Setup & Functions
# --------------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc):
    print("[MQTT] Connected to ThingSpeak MQTT Broker!")

def on_disconnect(client, userdata, rc):
    print("[MQTT] Disconnected from MQTT Broker! Will retry...")
    time.sleep(5)
    connect_mqtt()

def connect_mqtt():
    try:
        print("[MQTT] Attempting to connect...")
        mqtt_client.connect()
        print("[MQTT] Connected!")
    except Exception as e:
        print("[MQTT] Connection error:", e)
        time.sleep(5)
        connect_mqtt()

def publish_field8(value):
    """
    Publishes the given value to field 8 on the specified ThingSpeak channel.
    """
    payload = f"api_key={WRITE_API_KEY_FIELD8}&field8={value}"
    print(f"[MQTT] Publishing to {MQTT_TOPIC}")
    print(f"       Payload = {payload}")
    try:
        mqtt_client.publish(MQTT_TOPIC, payload)
        print("[MQTT] Publish successful!\n")
    except Exception as e:
        print("[MQTT] Publish error:", e)
        mqtt_client.disconnect()
        time.sleep(5)
        connect_mqtt()

# --------------------------------------------------------------------------------
#                                Main
# --------------------------------------------------------------------------------
def main():
    global mqtt_client

    # 1) Connect Wi-Fi
    connect_wifi()

    # 2) Create SocketPool
    pool = socketpool.SocketPool(wifi.radio)

    # 3) Sync Time via NTP
    sync_time(pool)

    # 4) Setup MQTT client
    mqtt_client = MQTT.MQTT(
        broker=MQTT_BROKER,
        port=MQTT_PORT,
        username=USERNAME,
        password=PASSWORD_MQTT,
        client_id=CLIENT_ID,
        socket_pool=pool,
    )
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    connect_mqtt()

    # This value will be adjusted by short/long button presses (from the NEW button on GP12).
    field8_value = 90

    # Track state for original (GP14) button
    last_button_state = False
    # Keep track of the LED's ON/OFF state
    led_button_state = False

    # Track state for new button (GP12)
    last_button2_state = False
    press_start_time2 = 0.0
    HOLD_TIME = 2.0  # 2 seconds for short/long press

    while True:
        try:
            # -------------------------------------------------------------------
            # 1) Read BMP280 data (ONLY display on LCD)
            # -------------------------------------------------------------------
            temperature = bmp.temperature      # in Celsius
            pressure = bmp.pressure            # in hPa
            print(f"Temp: {temperature:.2f}°C, Pressure: {pressure:.2f}hPa")

            # -------------------------------------------------------------------
            # 2) Get current local time
            # -------------------------------------------------------------------
            current_time = time.localtime()
            hour = current_time.tm_hour
            minute = current_time.tm_min
            print(f"Current Time: {hour:02}:{minute:02}")

            # -------------------------------------------------------------------
            # 3) Update Nokia LCD (temperature, pressure, and time)
            # -------------------------------------------------------------------
            update_lcd(temperature, pressure, hour, minute)

            # -------------------------------------------------------------------
            # 4) Time-based LED on GP15 (ON between 07:00 and 19:00)
            # -------------------------------------------------------------------
            if 7 <= hour < 19:
                led_time.value = True
                print("Time-based LED turned ON!")
            else:
                led_time.value = False
                print("Time-based LED turned OFF!")

            # -------------------------------------------------------------------
            # 5) Original BUTTON (GP14) logic
            #
            #    - Toggles the LED on GP13 on every press
            #    - No MQTT logic on this button
            # -------------------------------------------------------------------
            current_button_state = button.value

            # RISING EDGE (False -> True) => Press
            if current_button_state and not last_button_state:
                # Toggle the LED immediately on press
                led_button_state = not led_button_state
                led_button.value = led_button_state
                print("Original button pressed - LED toggled",
                      "ON" if led_button_state else "OFF")

            last_button_state = current_button_state

            # -------------------------------------------------------------------
            # 6) NEW BUTTON (GP12) logic:
            #
            #    We detect:
            #      - RISING EDGE => record press_start_time2
            #      - FALLING EDGE => check how long it was held:
            #           < 2s  => increment field8_value by 10
            #           >=2s => decrement field8_value by 10
            #      - Publish to field 8 via MQTT on release
            # -------------------------------------------------------------------
            current_button2_state = button2.value

            # RISING EDGE (False -> True) => Press start
            if current_button2_state and not last_button2_state:
                press_start_time2 = time.monotonic()
                print("New button (GP12) pressed - waiting to detect short/long press")

            # FALLING EDGE (True -> False) => release
            if (not current_button2_state) and last_button2_state:
                press_duration = time.monotonic() - press_start_time2

                if press_duration >= HOLD_TIME:
                    # LONG press
                    field8_value -= 10
                    print(f"New button HELD for {press_duration:.2f}s => decrement field8_value by 10.")
                else:
                    # SHORT press
                    field8_value += 10
                    print(f"New button PRESSED for {press_duration:.2f}s => increment field8_value by 10.")

                print(f"New field8_value = {field8_value}")
                # Publish immediately to ThingSpeak (field 8 via MQTT)
                publish_field8(field8_value)

            last_button2_state = current_button2_state

        except Exception as e:
            print("Main loop error:", e)

        # Small delay to help with button debouncing & reduce CPU usage
        time.sleep(0.1)

# --------------------------------------------------------------------------------
# Run the program
# --------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
