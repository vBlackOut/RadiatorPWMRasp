#!/usr/bin/env python
from subprocess import Popen, PIPE, STDOUT
import RPi.GPIO as GPIO
import time
import smbus
import signal
from lib.daemon import *
from datetime import datetime, timedelta
from pyowm import OWM
from pyowm.utils import config
from pyowm.utils import timestamps

def handler(signum, frame):
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(16, GPIO.OUT)
    GPIO.output(16, False)
    GPIO.cleanup()

signal.signal(signal.SIGHUP, handler)

class radiateur(Daemon):

    def __init__(self, pwm, times):
        self.pwm = pwm
        self.back_pwm = pwm
        self.timing = 0
        self.times = times
        self.back_times = times

        self.pidfile = "/tmp/daemon-rad_python.pid"
        self.sysargv = sys.argv
        self.stderr = "/tmp/error.log"
        self.stdout = "/tmp/outrad.log"
        self.time_start = datetime.now()
        self.date_stamp = 0
        self.min_temp = 0
        self.date_checktemp = datetime.now()
        self.weath = {}

        super().__init__(pidfile=self.pidfile, sysargv=self.sysargv, stderr=self.stderr, stdout=self.stdout)

    def get_weath(self):
        owm = OWM('')
        mgr = owm.weather_manager()

        try:
            observation = mgr.weather_at_place('Poissy,FR')
            w = observation.weather

            details = {
                "temps": w.detailed_status,
                "vent": w.wind(),
                "humidity": w.humidity,
                "temp": w.temperature('celsius'),
                "pluis": w.rain,
                "chaleur_index": w.heat_index,
                "nuage": w.clouds
            }

            self.weath = details

            return details
        except:
            return self.weath

    def CRC(self, data):
      crc = 0xff
      for s in data:
        crc ^= s
        for i in range(8):
          if crc & 0x80:
            crc <<= 1
            crc ^= 0x131
          else:
            crc <<= 1
      return crc


    def check_sht35(self, bus, msg):
        # SHT3x hex adres
        SHT3x_ADDR		= 0x44
        SHT3x_SS		= 0x2C
        SHT3x_HIGH		= 0x06
        SHT3x_READ		= 0x00
        # MS to SL

        # Read out data
        for i in range(0, 100):
            try:
                #bus.write_i2c_block_data(SHT3x_ADDR, SHT3x_SS, [0x06])
                bus.write_i2c_block_data(SHT3x_ADDR, 0x24, [0x00])
                time.sleep(0.5)
                dataT = bus.read_i2c_block_data(SHT3x_ADDR, SHT3x_READ, 6)
                break
            except:
                pass

        if dataT[2] != self.CRC(dataT[:2]):
            raise RuntimeError("temperature CRC mismatch")
        if dataT[5] != self.CRC(dataT[3:5]):
            raise RuntimeError("humidity CRC mismatch")

        # Devide data into counts Temperature
        t_data = dataT[0] << 8 | dataT[1]

        # Devide data into counts Humidity
        h_data = dataT[3] << 8 | dataT[4]

        # Convert counts to Temperature/Humidity
        Humidity = 100.0*float(h_data)/65535.0
        Temperature = (175.0 * float(t_data) / 65535.0) - 45
        self.writetemp(round(Temperature,2), round(Humidity))
        print("{} Temp: {}  H: {}".format(msg, round(Temperature, 2), round(Humidity)))

        return Temperature, Humidity

    def writetemp(self, temp, hum):
        f = open("/home/pi/Python/radiateur/temp.log", "w")
        f.write("Temp: {} H: {}".format(temp, hum))
        f.close()

    def timingpwm(self):
        print("calcule par kwh pwm {}%".format(self.pwm))

        #calcule kwh to timing
        pwm = self.pwm
        calculepwm = (2300*pwm)/100
        kwh = calculepwm

        voltage = 230
        ampere = 10
        ah = (kwh/voltage)
        coulomb = ah*3600

        try:
            TA = (coulomb/ampere)
            Timing = round(3600/TA,2)
            print("timing par secondes :", round(3600/TA,2)) # result 3.5

        except ZeroDivisionError:
            TA = (coulomb/1)
            Timing = 0

        print("kwh :", calculepwm/1000)

        return Timing

    def updatetiming(self, value):
        self.pwm = value
        self.timing = self.timingpwm()

    def get_sec(self, time_str):
        """Get Seconds from time."""
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)

    def get_time(self, sec):
        # create timedelta and convert it into string
        td_str = str(timedelta(seconds=sec))
        #print('Time in seconds:', sec)

        # split string into individual component
        x = td_str.split(':')
        print('temps restant:', x[0], 'Heures', x[1], 'Minutes', round(float(x[2])), 'Secondes')


    def run(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(16, GPIO.OUT)
        GPIO.output(16, False)

        self.timing = self.timingpwm()
        bus = smbus.SMBus(1)
        temp = 20
        temp, hum = self.check_sht35(bus, "temp1")
        meteo = self.get_weath()

        try:
            self.date_stamp = datetime.now() - self.time_start
            count = 0

            while True:
                self.checktemp = datetime.now() - self.date_checktemp

                if self.checktemp.total_seconds() > 300:
                    self.date_checktemp = datetime.now()
                    meteo = self.get_weath()

                if meteo['temp']['temp'] > 12:
                    time.sleep(3)
                    print("temp exterieur stop > 12°C")
                    continue

                print("temperature exterieur {} H:{}".format(meteo['temp']['temp'], meteo['humidity']))
                temp, hum = self.check_sht35(bus, "temp1")

                if self.min_temp != 0 and temp > self.min_temp:
                    time.sleep(3)
                    print("wait min temp {} > {}".format(round(temp,2), self.min_temp))
                    continue
                elif temp < self.min_temp:
                    self.min_temp = 0

                if self.date_stamp.total_seconds() < self.get_sec(self.times):
                    self.date_stamp = datetime.now() - self.time_start

                    self.get_time(self.get_sec(self.times) - self.date_stamp.total_seconds())

                    if (temp < 19.25):

                        # if self.pwm == 0 and temp <= 19.3:
                        #     self.updatetiming(self.back_pwm)

                        if self.pwm == 0:
                            # GPIO 0
                            GPIO.output(16, False)
                            time.sleep(1)
                        elif self.pwm != 100:
                            self.timing = self.timingpwm()
                            # GPIO 1
                            GPIO.output(16, True)
                            time.sleep(1)
                            # GPIO 0
                            GPIO.output(16, False)
                            time.sleep(self.timing)
                        elif self.pwm == 100:
                            # GPIO 0
                            GPIO.output(16, True)
                            time.sleep(self.timing)
                        else:
                            GPIO.output(16, False)
                            time.sleep(self.timing)

                    else:
                        #self.updatetiming(0)
                        print("stop")
                        GPIO.output(16, False)
                        self.min_temp = 19

                elif self.date_stamp.total_seconds() > self.get_sec(self.times) and temp <= 19.25:
                    if count <= 2:
                        self.pwm = round(self.back_pwm / 2)
                        self.timing = self.timingpwm()
                        self.time_start = datetime.now()
                        self.date_stamp = datetime.now() - self.time_start
                        self.times = "0:10:0"
                        count = count + 1
                        time.sleep(3)
                    else:
                        check_date = datetime.now()
                        if check_date.hour >= 20 or check_date.hour <= 7:
                            count = 0
                            self.timing = self.timingpwm()
                            self.time_start = datetime.now()
                            self.date_stamp = datetime.now() - self.time_start
                            self.times = self.back_times
                            self.updatetiming(self.back_pwm)
                            time.sleep(1)

                else:
                    check_date = datetime.now()
                    if check_date.hour >= 20 or check_date.hour <= 7:
                        self.time_start = datetime.now()
                        self.date_stamp = datetime.now() - self.time_start
                        self.times = self.back_times
                        self.updatetiming(self.back_pwm)
                        time.sleep(1)

            self.exit()

        except KeyboardInterrupt:
            self.exit()

    def exit(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(16, GPIO.OUT)
        GPIO.output(16, False)
        GPIO.cleanup()

if __name__ == "__main__":
    rad = radiateur(45, "1:00:00")
