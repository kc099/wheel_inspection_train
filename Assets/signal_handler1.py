import threading
import time
import traceback
import json
import logging
import sys
from pathlib import Path

# Set up logger
logger = logging.getLogger(__name__)


def get_app_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

# Try importing pyserial
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("pyserial library not found - 24V signal detection will not be available")

class SignalHandler:
    """Handler for external 24V signal detection and communication"""
    
    def __init__(self, signal_callback=None):
        """
        Initialize signal handler
        
        Args:
            signal_callback: Function to call when 24V signal is detected
        """
        self.signal_callback = signal_callback
        self.thread = None
        self.stop_flag = False
        self.is_running = False
        self.serial_port = None
        self.settings = self._load_settings()
        self.is_processing_signal = False  # Flag to track signal processing state
        self.last_callback_time = 0       # Track last callback time globally
        
        # State tracking for wheel presence detection
        self.current_sensor_state = False  # True if wheel/signal detected, False if idle
        self.last_signal_time = 0          # Timestamp of last detected signal
        self.signal_timeout = 30.0         # Timeout: signal must be received within 30 seconds to stay "active"
        
        print(f"[SignalHandler] Initialized with callback: {signal_callback is not None}")
        if signal_callback:
            print(f"[SignalHandler] Callback function: {signal_callback.__name__}")
        
    def _load_settings(self):
        """Load settings from settings.json"""
        try:
            settings_path = get_app_path() / "settings.json"
            with open(settings_path, "r") as f:
                data = json.load(f)
                
                # Get settings from the nested structure
                if isinstance(data, dict):
                    if "settings" in data:
                        settings = data["settings"]
                    else:
                        settings = data  # Handle case where settings are at root level
                        
                    # Ensure we have all required settings with defaults
                    settings = {
                        "com_port": settings.get("com_port"),
                        "baud_rate": settings.get("baud_rate", 19200),
                        "modbus_slave_id": settings.get("modbus_slave_id", 1)
                    }
                    
                    # Print loaded settings for debugging
                    print(f"Loaded settings - COM port: {settings['com_port']}, "
                          f"Baud rate: {settings['baud_rate']}, "
                          f"Slave ID: {settings['modbus_slave_id']}")
                    return settings
                    
        except Exception as e:
            print(f"Error loading settings: {e}")
            traceback.print_exc()
            
        # Return default settings if loading fails
        return {"com_port": None, "modbus_slave_id": 1, "baud_rate": 19200}
        
    def is_wheel_present(self):
        """
        Check if a wheel is currently present in the machine.
        
        Returns True if a signal has been detected within the timeout period,
        False if no signal has been detected or the timeout has expired.
        
        Returns:
            bool: True if wheel/signal detected, False if idle/no signal
        """
        current_time = time.time()
        
        # If no signal received yet, return not present
        if self.last_signal_time == 0:
            return False
        
        # Check if signal is still "active" (within timeout period)
        time_since_signal = current_time - self.last_signal_time
        
        if time_since_signal <= self.signal_timeout:
            # Signal is recent, wheel is present
            return True
        else:
            # Signal has timed out, wheel is not present
            self.current_sensor_state = False
            return False
        
    def start_detection(self):
        """Start 24V signal detection thread"""
        if not SERIAL_AVAILABLE:
            print("24V signal detection not available (pyserial not installed)")
            return False
            
        if self.is_running:
            print("Signal detection already running")
            return True
            
        # Check if we have a valid COM port
        com_port = self.settings.get("com_port")
        
        # If no COM port is selected, try to auto-detect
        if not com_port:
            try:
                available_ports = [port.device for port in serial.tools.list_ports.comports()]
                if available_ports:
                    com_port = available_ports[0]  # Use the first available port
                    print(f"Auto-detected COM port: {com_port}")
                    # Update settings with the detected port
                    self.settings["com_port"] = com_port
                #  available_ports = [port.device for port in serial.tools.list_ports.comports()]
                # for port in available_ports:
                #     if "Arduino" in port.description or 'USB' in port.description:
                #         com_port = port
                #     # com_port = available_ports[0]  # Use the first available port
                #     print(f"Auto-detected COM port: {com_port}")
                #     # Update settings with the detected port
                #     self.settings["com_port"] = com_port
                #     # Save the updated settings    # Save the updated settings
                    # try:
                    #     with open("settings.json", "r") as f:
                    #         data = json.load(f)
                    #     if "settings" in data:
                    #         data["settings"]["com_port"] = com_port
                    #         with open("settings.json", "w") as f:
                    #             json.dump(data, f, indent=4)
                    #         print(f"Updated settings.json with auto-detected COM port: {com_port}")
                    # except Exception as e:
                    #     print(f"Error saving auto-detected COM port: {e}")
                else:
                    print("No COM ports available")
                    return False
            except Exception as e:
                print(f"Error checking available ports: {e}")
                return False
            
        # Verify port exists
        try:
            available_ports = [port.device for port in serial.tools.list_ports.comports()]
            if com_port not in available_ports:
                print(f"Configured COM port {com_port} not found. Available ports: {available_ports}")
                return False
        except Exception as e:
            print(f"Error checking available ports: {e}")
            return False
            
        self.stop_flag = False
        self.thread = threading.Thread(target=self._detect_signal_thread, daemon=True)
        self.thread.start()
        self.is_running = True
        return True
        
    def stop_detection(self):
        """Stop 24V signal detection thread"""
        self.stop_flag = True
        if self.thread:
            self.thread.join(timeout=1.0)   # timeout is one second. after  taking frame it will send the frame
        self.is_running = False
        self.is_processing_signal = False  # Reset processing state
        
    def reset_processing_state(self):
        """Manually reset the processing state (useful for debugging)"""
        self.is_processing_signal = False
        print("Processing state reset manually")
        
    def _detect_signal_thread(self):
        """Thread for monitoring 24V signal with frame buffering and cooldown"""
        if not SERIAL_AVAILABLE:
            return
            
        try:
            # Get COM port and baud rate from settings
            com_port = self.settings.get("com_port", "COM8")  # Use settings, fallback to COM8
            baud_rate = self.settings.get("baud_rate", 19200)
            slave_id = self.settings.get("modbus_slave_id", 1)
            print(f"Starting signal detection with slave ID: {slave_id}")
            
            if not com_port:
                print("No COM port selected in settings")
                return
                
            # Verify port exists
            available_ports = [port.device for port in serial.tools.list_ports.comports()]
            if com_port not in available_ports:
                print(f"Selected COM port {com_port} not found")
                return
            
            # Open the serial port with improved settings
            self.serial_port = serial.Serial(
                port=com_port,
                baudrate=baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,        # Increased timeout to prevent blocking
                write_timeout=0.5,  # Add write timeout
                inter_byte_timeout=0.1  # Timeout between bytes
            )
            print(f"Connected to {com_port} at {baud_rate} baud for 24V signal detection")
            
            # Clear any existing data in the buffer
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
            print("Serial buffers cleared")
            
            # === DIAGNOSTIC TEST ===
            print("\n" + "="*60)
            print("SERIAL PORT DIAGNOSTIC TEST")
            print("="*60)
            print(f"Testing COM port: {com_port}")
            print(f"Baud rate: {baud_rate}")
            print(f"Parity: EVEN")
            print(f"Stop bits: 1")
            print(f"Data bits: 8")
            print("Waiting 2 seconds for incoming data...")
            print("(If sensor sends trigger now, it should appear below)")
            
            # Wait and listen for 2 seconds
            test_start = time.time()
            while time.time() - test_start < 2.0:
                if self.serial_port.in_waiting > 0:
                    test_data = self.serial_port.read(self.serial_port.in_waiting)
                    hex_test = ' '.join([f'{b:02X}' for b in test_data])
                    print(f"✓ DATA RECEIVED: {len(test_data)} bytes - {hex_test}")
                time.sleep(0.01)
            
            print("=" * 60 + "\n")
            
            # Initialize frame buffering variables
            data_buffer = bytearray()
            last_callback_time = 0
            callback_cooldown = 4  # 4 seconds cooldown between callbacks
            min_frame_size = 6        # Minimum frame size (6 bytes for trigger signal)
            max_frame_size = 8        # Maximum expected frame size (8 bytes for trigger signal)
            frame_timeout = 2.0       # Time to wait for complete frame
            last_data_time = 0        # Track when we last received data
            
            print("Modbus monitoring started - waiting for complete frames (6-8 bytes for trigger signal)...")
            print(f"Signal callback registered: {self.signal_callback is not None}")
            
            heartbeat_counter = 0
            
            while not self.stop_flag:
                try:
                    current_time = time.time()
                    
                    # Check if there's any data on the serial port
                    bytes_waiting = self.serial_port.in_waiting
                    
                    # Debug: Show port status every second
                    heartbeat_counter += 1
                    if heartbeat_counter % 100 == 0:  # Every 1 second (100 * 0.01s)
                        print(f"[SIGNAL] Port active - Bytes waiting: {bytes_waiting}, Buffer: {len(data_buffer)}")
                    
                    if bytes_waiting > 0:
                        # Read all available data
                        new_data = self.serial_port.read(bytes_waiting)
                        if len(new_data) > 0:
                            data_buffer.extend(new_data)
                            last_data_time = current_time
                            
                            # Debug: Print received data
                            hex_data = ' '.join([f'{b:02X}' for b in new_data])
                            print(f"[SIGNAL] Received {len(new_data)} bytes: {hex_data}")
                            print(f"[SIGNAL] Buffer now contains {len(data_buffer)} bytes total")
                    
                    # Check if we have enough data for a complete frame
                    if len(data_buffer) >= min_frame_size:
                        # Check cooldown period
                        time_since_last_callback = current_time - last_callback_time
                        
                        if time_since_last_callback >= callback_cooldown:
                            print(f"Complete frame detected: {len(data_buffer)} bytes")
                            
                            # Print the complete frame in hex
                            hex_frame = ' '.join([f'{b:02X}' for b in data_buffer])
                            print(f"Frame data (hex): {hex_frame}")
                            
                            # === UPDATE STATE: Signal detected - wheel present ===
                            self.current_sensor_state = True
                            self.last_signal_time = current_time
                            print(f"[STATE] Wheel presence updated: PRESENT (signal received at {current_time:.2f})")
                            
                            # Trigger the callback
                            if self.signal_callback and not self.is_processing_signal:
                                self.is_processing_signal = True
                                print("🔔 CALLBACK TRIGGERED - Invoking on_external_signal_detected()...")
                                print(f"   Callback function: {self.signal_callback}")
                                try:
                                    result = self.signal_callback(signal_type="MODBUS_FRAME")
                                    print(f"✓ Callback executed successfully (returned: {result})")
                                except Exception as e:
                                    print(f"✗ ERROR in callback: {e}")
                                    # import traceback
                                    traceback.print_exc()
                                    logger.error(f"Error in signal callback: {e}", exc_info=True)
                                finally:
                                    last_callback_time = current_time
                                
                                # Reset processing flag after a short delay
                                def reset_processing_flag():
                                    time.sleep(1.0)  # Wait 1 second
                                    self.is_processing_signal = False
                                
                                # Reset flag in background
                                threading.Thread(target=reset_processing_flag, daemon=True).start()
                            elif self.is_processing_signal:
                                print("⚠ Frame received but already processing - ignoring")
                            else:
                                print("✗ No callback registered!")
                            
                            # Clear the buffer after processing
                            data_buffer.clear()
                            print("Buffer cleared after processing")
                            
                        else:
                            remaining_cooldown = callback_cooldown - time_since_last_callback
                            print(f"Frame received but still in cooldown: {remaining_cooldown:.1f}s remaining")
                            # Clear buffer even during cooldown to prevent accumulation
                            data_buffer.clear()
                    
                    # Handle incomplete frames that timeout
                    elif len(data_buffer) > 0:
                        time_since_last_data = current_time - last_data_time
                        
                        if time_since_last_data > frame_timeout:
                            print(f"Incomplete frame timeout: {len(data_buffer)} bytes (less than {min_frame_size})")
                            hex_incomplete = ' '.join([f'{b:02X}' for b in data_buffer])
                            print(f"Discarded incomplete frame: {hex_incomplete}")
                            data_buffer.clear()
                    
                    # Prevent buffer from growing too large
                    if len(data_buffer) > max_frame_size * 2:  # Allow some overhead
                        print(f"Buffer overflow protection: clearing {len(data_buffer)} bytes")
                        data_buffer.clear()
                    
                    # Short sleep to prevent CPU overuse
                    time.sleep(0.01)  # 10ms sleep
                    
                    heartbeat_counter += 1
                    if heartbeat_counter % 500 == 0:  # Every 5 seconds (500 * 0.01s)
                        print(f"[SIGNAL] Monitoring... (buffer: {len(data_buffer)} bytes, waiting: {bytes_waiting})")
                        
                except serial.SerialTimeoutException:
                    # This is normal - just means no data within timeout period
                    continue
                except serial.SerialException as e:
                    print(f"Serial port error: {e}")
                    # Try to recover by reopening the port
                    try:
                        if self.serial_port:
                            self.serial_port.close()
                        time.sleep(1.0)  # Wait before retry
                        
                        # Reopen the port
                        self.serial_port = serial.Serial(
                            port=com_port,
                            baudrate=baud_rate,
                            bytesize=serial.EIGHTBITS,
                            parity=serial.PARITY_EVEN,
                            stopbits=serial.STOPBITS_ONE,
                            timeout=0.5,
                            write_timeout=0.5,
                            inter_byte_timeout=0.1
                        )
                        print("Serial port reopened after error")
                        data_buffer.clear()  # Clear buffer after reconnection
                        
                    except Exception as reconnect_error:
                        print(f"Failed to reconnect serial port: {reconnect_error}")
                        break
                        
                except Exception as e:
                    print(f"Error reading signal data: {e}")
                    traceback.print_exc()
                    time.sleep(0.1)  # Wait a bit before retrying 
                
            # Clean up
            if self.serial_port:
                self.serial_port.close()
                self.serial_port = None
                print("Serial port closed")
                
        except Exception as e:
            print(f"Error in 24V signal detection: {e}")
            traceback.print_exc()
        
        finally:
            # Ensure cleanup
            if self.serial_port:
                try:
                    self.serial_port.close()
                except:
                    pass
            self.is_running = False
            print("Signal detection thread exited")

    def _process_modbus_data(self, data, expected_slave_id):
        """Process received data to find valid Modbus frames"""
        try:
            # Look for potential Modbus frames in the received data
            for i in range(len(data) - 1):  # Need at least 2 bytes for slave ID and function code
                if i + 7 < len(data):  # Check if we have enough bytes for a minimum frame
                    potential_frame = data[i:i+8]  # Try 8-byte frame first
                    
                    slave_id = potential_frame[0]
                    function_code = potential_frame[1]
                    
                    # print(f"Checking potential frame at offset {i}: "
                    #     f"Slave ID: {slave_id}, Function: 0x{function_code:02X}")
                    
                    # Check for read holding registers request (0x03) to configured slave ID
                    if slave_id == expected_slave_id and function_code == 0x03:
                        # print(f"Valid Modbus read request detected - Slave ID: {slave_id}, Function: 0x03")
                        # Trigger the callback function if it exists
                        if self.signal_callback:
                            self.signal_callback(signal_type="MODBUS_FRAME")
                        return  # Found valid frame, exit processing
                    
                    # Check for other common Modbus function codes
                    elif slave_id == expected_slave_id and function_code in [0x01, 0x02, 0x04, 0x05, 0x06, 0x0F, 0x10]:
                        print(f"Modbus frame for correct slave ID - Slave: {slave_id}, Function: 0x{function_code:02X}")
                        # Could trigger callback for any valid Modbus frame to slave
                        # if self.signal_callback:
                        #     self.signal_callback(signal_type="MODBUS_FRAME")
                    
            # If no valid frame found, just log the data
            print(f"No valid Modbus frame found in {len(data)} bytes")
            
        except Exception as e:
            print(f"Error processing Modbus data: {e}")
            traceback.print_exc()

    def test_serial_connection(self):
        """Test method to verify serial connection is working"""
        if not self.serial_port or not self.serial_port.is_open:
            print("Serial port not open for testing")
            return False
        
        try:
            # Send a test command (query device status)
            # This is a simple Modbus read request - adjust as needed
            test_command = bytes([self.settings.get("modbus_slave_id", 1), 0x03, 0x00, 0x00, 0x00, 0x01])
            
            # Calculate CRC for the test command (simplified)
            crc = self._calculate_modbus_crc(test_command)
            crc_bytes = struct.pack('<H', crc)
            complete_command = test_command + crc_bytes
            
            print(f"Sending test command: {' '.join([f'{b:02X}' for b in complete_command])}")
            self.serial_port.write(complete_command)
            
            # Wait for response
            time.sleep(0.5)
            if self.serial_port.in_waiting > 0:
                response = self.serial_port.read(self.serial_port.in_waiting)
                print(f"Received test response: {' '.join([f'{b:02X}' for b in response])}")
                return True
            else:
                print("No response to test command")
                return False
                
        except Exception as e:
            print(f"Error in serial test: {e}")
            return False
            
    def send_measurement_data(self, model_name, diameter_mm, height_mm, is_pass=False, detection_confirmed=True):
        """Send measurement data via Modbus frame (13 bytes) - MATCHES ARDUINO FIRMWARE
        
        Frame structure (13 bytes total - MUST match Arduino rxdata[13]):
        - Byte 0: Slave ID (1 byte)
        - Byte 1: Function code (1 byte): 0x03
        - Byte 2-5: Height (4 bytes, IEEE754 float, Big-Endian)
        - Byte 6-9: Diameter (4 bytes, IEEE754 float, Big-Endian)
        - Byte 10: Model ID (1 byte)
        - Byte 11: Status (1 byte): 0x01 if PASS, 0x00 if FAIL
        - Byte 12: Detection Confirmation (1 byte): 0x01 if CONFIRMED, 0x00 if NOT CONFIRMED
        
        CRITICAL: This frame structure MUST exactly match Arduino's rxdata array!
        Arduino expects: rxdata[0]=SlaveID, rxdata[1]=FuncCode, 
                       rxdata[2-5]=Height(float), rxdata[6-9]=Diameter(float),
                       rxdata[10]=Model, rxdata[11]=Status, rxdata[12]=DetectionConfirmed
        
        Args:
            model_name: Name of the wheel model (e.g., "Gray Wheel", "Gray1 Wheel")
            diameter_mm: Measured diameter in mm (float)
            height_mm: Measured height in mm (float)
            is_pass: Boolean indicating if measurements passed validation
            detection_confirmed: Boolean (0x01 if detection succeeded, 0x00 if failed)
            
        Returns:
            bool: True if data was sent successfully, False otherwise
        """
        if not SERIAL_AVAILABLE:
            print("Cannot send measurement data - pyserial not installed")
            return False
        
        # Check if we have an open serial connection
        if self.serial_port is None or not self.serial_port.is_open:
            print("Cannot send measurement data - serial port not open")
            print("The signal detection system must be running to send data")
            return False
            
        try:
            import struct
            
            # Get Modbus parameters
            SLAVE_ADDRESS = self.settings.get("modbus_slave_id", 1)
            FUNCTION_CODE = 0x03  # Read Holding Registers
            
            # Get compressed model ID
            model_id = self._parse_model_name(model_name)
            
            # === CRITICAL FIX: Height BEFORE Diameter, BOTH as 4-byte floats ===
            # Convert height to 4-byte IEEE754 float, Big-Endian
            height_bytes = struct.pack('>f', float(height_mm))
            
            # Convert diameter to 4-byte IEEE754 float, Big-Endian
            diameter_bytes = struct.pack('>f', float(diameter_mm))
            
            # Create status byte: 0x01 if PASS, 0x00 if FAIL
            status_byte = 0x01 if is_pass else 0x00
            
            # Detection Confirmation byte: 0x01 if CONFIRMED, 0x00 if NOT CONFIRMED
            confirmation_byte = 0x01 if detection_confirmed else 0x00
            
            # === CRITICAL: Frame MUST be exactly 13 bytes to match Arduino ===
            # Order: [SlaveID, FuncCode] + Height(4) + Diameter(4) + [Model, Status, Confirmation]
            frame = bytes([
                SLAVE_ADDRESS,    # Byte 0: Slave ID
                FUNCTION_CODE,    # Byte 1: Function code (0x03)
            ]) + height_bytes + diameter_bytes + bytes([
                model_id,         # Byte 10: Model ID
                status_byte,      # Byte 11: Status (0x01=PASS, 0x00=FAIL)
                confirmation_byte # Byte 12: Detection Confirmation (0x01=Confirmed, 0x00=Not Confirmed)
            ])
            
            # Verify frame is exactly 13 bytes
            if len(frame) != 13:
                print(f"❌ ERROR: Frame is {len(frame)} bytes, must be exactly 13!")
                logger.error(f"Frame size mismatch: {len(frame)} bytes (expected 13)")
                return False
            
            # Send the frame to Arduino
            self.serial_port.write(frame)
            
            print(f"\n📤 Sent 13-byte measurement frame (Arduino Schneider):")
            print(f"    Byte 0   - Slave ID: 0x{frame[0]:02X} ({frame[0]})")
            print(f"    Byte 1   - Function: 0x{frame[1]:02X} ({frame[1]})")
            print(f"    Byte 2-5 - Height: {' '.join([f'{b:02X}' for b in frame[2:6]])} ({height_mm:.2f}mm)")
            print(f"    Byte 6-9 - Diameter: {' '.join([f'{b:02X}' for b in frame[6:10]])} ({diameter_mm:.2f}mm)")
            print(f"    Byte 10  - Model ID: {frame[10]} ({model_name})")
            print(f"    Byte 11  - Status: 0x{frame[11]:02X} ({'PASS ✓' if is_pass else 'FAIL ✗'})")
            print(f"    Byte 12  - Detection Confirmed: 0x{frame[12]:02X} ({'✅ YES' if frame[12] == 0x01 else '❌ NO'})")
            print(f"    Total: {len(frame)} bytes")
            
            logger.info(f"Sent 13-byte measurement frame: Height={height_mm:.2f}mm, Diameter={diameter_mm:.2f}mm, Model={model_name}, Status={'PASS' if is_pass else 'FAIL'}, Detection={'CONFIRMED' if detection_confirmed else 'NOT CONFIRMED'}")
            
            return True
            
        except Exception as e:
            print(f"Error sending measurement data: {e}")
            traceback.print_exc()
            return False

    def _parse_model_name(self, model_name):
        """Parse model name to unique model ID (compressed)
        
        Maps each wheel model to UNIQUE ID:
        - 'Gray Wheel' → 1
        - 'Gray1 Wheel' → 2
        - 'Aluminum Wheel' → 5
        - 'Steel Wheel' → 6
        
        Args:
            model_name: Model name like "Gray Wheel", "Gray1 Wheel", etc.
            
        Returns:
            int: Unique model ID (0-255)
        """
        try:
            # Model mapping - UNIQUE ID per model
            model_mapping = {
                'A1': 1,
                'A2': 2,
                'B1': 3,
                'B2': 4,
                'C1': 5,
            }
            
            if model_name in model_mapping:
                model_id = model_mapping[model_name]
                logger.info(f"Model '{model_name}' → Unique ID: {model_id}")
                return model_id
            
            # Fallback: Extract first number from model name
            import re
            numbers = re.findall(r'\d+', model_name)
            if numbers:
                model_id = int(numbers[0]) % 256  # Keep within byte range
                logger.info(f"Model '{model_name}' → ID: {model_id}")
                return model_id
                
        except Exception as e:
            logger.error(f"Error parsing model name '{model_name}': {e}")
        
        logger.warning(f"Could not parse model name '{model_name}', using default ID: 0")
        return 0
    
    def _calculate_modbus_crc(self, data):
        """Calculate Modbus RTU CRC-16 checksum
        
        Args:
            data: Bytes to calculate CRC for
            
        Returns:
            int: 16-bit CRC value
        """
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001  # Polynomial for Modbus CRC-16
                else:
                    crc >>= 1
        return crc
