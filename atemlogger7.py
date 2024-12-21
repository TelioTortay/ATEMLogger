import time
import logging
from PyATEMMax import ATEMMax
import socket
import threading
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox, QGroupBox, QListWidget, QTableWidget, QTableWidgetItem, QFrame, QCheckBox
from PyQt6.QtGui import QColor, QFont

# Basic logging configuration
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("ATEMLogger")

# Function to generate an EDL file with unique identifiers for each clip
def generate_edl(clips, file_path, compensation_frames=0):
    if not clips:
        log.warning("No cuts detected, no data to save in the EDL.")
        return
    
    with open(file_path, 'w') as edl_file:
        # EDL header
        edl_file.write("TITLE: ATEM Program Output\n")
        edl_file.write("FCM: NON-DROP FRAME\n")
        
        for i, clip in enumerate(clips):
            start_timecode = clip['start']
            end_timecode = clip['end']

            # If compensation is enabled, adjust the timecode
            if compensation_frames > 0:
                start_timecode = adjust_timecode(start_timecode, compensation_frames)
                end_timecode = adjust_timecode(end_timecode, compensation_frames)

            # Generate a unique ID for each clip
            clip_id = f"{i+1:04}"
            edl_file.write(f"{clip_id}  AX    V     C   {start_timecode} {end_timecode} {start_timecode} {end_timecode}\n")
            edl_file.write(f"* FROM CLIP NAME: {clip['src']}\n")

    log.info(f"EDL file successfully generated: {file_path}")

def adjust_timecode(timecode, compensation_frames):
    """
    Adjusts the timecode by adding the compensation for frames.
    """
    try:
        hours, minutes, seconds, frames = map(int, timecode.split(':'))
        frames += compensation_frames
        
        # Handle frame overflow
        if frames >= 25:  # Assumption: 25 frames per second
            frames -= 25
            seconds += 1

        # Handle second overflow
        if seconds >= 60:
            seconds -= 60
            minutes += 1

        # Handle minute overflow
        if minutes >= 60:
            minutes -= 60
            hours += 1

        # Format the adjusted timecode
        return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"

    except Exception as e:
        log.error(f"Error while adjusting the timecode: {e}")
        return timecode  # Return the original timecode in case of an error

def connect_to_hyperdeck(ip, port=9993):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((ip, port))
        # Read the initial response
        initial_response = s.recv(1024).decode('utf-8')
        print("Initial response from HyperDeck:", initial_response)
        return s
    except Exception as e:
        log.error(f"Error connecting to HyperDeck: {e}")
        return None

def get_timecode_from_hyperdeck(socket_conn):
    """
    Sends the "transport info" command and retrieves the "Display Timecode".
    """
    try:
        # Send the "transport info" command
        socket_conn.sendall(b"transport info\n")
        
        response = b""
        while True:
            chunk = socket_conn.recv(1024)
            response += chunk
            if not chunk or b"\n" in chunk:
                break

        # Decode and analyze the response
        response_str = response.decode("utf-8")
        print(f"Response received from HyperDeck: {response_str}")

        # Check and return the "display timecode"
        for line in response_str.split("\n"):
            if line.startswith("display timecode:"):
                return line.split("display timecode:")[1].strip()

        # If no display timecode is found, display an error message
        if "status:" in response_str:
            status = response_str.split("status:")[1].strip()
            print(f"Transport status: {status}")
            if "recording" in status:
                print("Recording in progress, displaying timecode.")
            else:
                print("HyperDeck in an unknown state or no timecode information.")

    except Exception as e:
        log.error(f"Error retrieving timecode: {e}")
    
    return None

class ATEMMonitorThread(QThread):
    update_input_signal = pyqtSignal(str)
    update_log_signal = pyqtSignal(str, str, str)
    update_timecode_signal = pyqtSignal(str)

    def __init__(self, atem_ip, hyperdeck_ip, stop_event, start_time, file_path, compensation_frames):
        super().__init__()
        self.atem_ip = atem_ip
        self.hyperdeck_ip = hyperdeck_ip
        self.stop_event = stop_event
        self.start_time = start_time
        self.file_path = file_path
        self.compensation_frames = compensation_frames
        self.hyperdeck_conn = None

    def run(self):
        atem = ATEMMax()
        try:
            atem.connect(self.atem_ip)
        except Exception as e:
            log.error(f"Error connecting to ATEM: {e}")
            self.stop_event.set()
            return

        self.hyperdeck_conn = connect_to_hyperdeck(self.hyperdeck_ip)
        if not self.hyperdeck_conn:
            log.error(f"Unable to connect to HyperDeck {self.hyperdeck_ip}.")
            self.stop_event.set()
            return

        last_program_input = None
        last_timecode = None
        clips = []

        try:
            while not self.stop_event.is_set():
                try:
                    program_input = atem.programInput[atem.atem.mixEffects.mixEffect1].videoSource
                    program_input_str = str(program_input) if not isinstance(program_input, str) else program_input

                    self.update_input_signal.emit(program_input_str)

                    timecode = get_timecode_from_hyperdeck(self.hyperdeck_conn)
                    if timecode:
                        self.update_timecode_signal.emit(timecode)

                    if program_input != last_program_input:
                        if last_program_input is not None and last_timecode:
                            clips.append({
                                'start': last_timecode,
                                'end': timecode,
                                'src': last_program_input
                            })
                            self.update_log_signal.emit(str(last_program_input), last_timecode, timecode)
                        last_program_input = program_input
                        last_timecode = timecode
                        log.info(f"Program input at {timecode}: {program_input_str}")
                    else:
                        time.sleep(0.01)
                except Exception as e:
                    log.error(f"Error retrieving program input: {e}")

            if last_program_input is not None and last_timecode:
                clips.append({
                    'start': last_timecode,
                    'end': timecode,
                    'src': last_program_input
                })

            if self.file_path and clips:
                generate_edl(clips, self.file_path, self.compensation_frames)

        except KeyboardInterrupt:
            pass
        finally:
            # Ensure proper disconnection
            atem.disconnect()
            if self.hyperdeck_conn:
                self.hyperdeck_conn.close()

class ATEMGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ATEM Logger")
        self.setGeometry(100, 100, 1200, 800)
        self.layout = QHBoxLayout()

        self.input_groupbox = QGroupBox("Video Inputs")
        self.input_layout = QVBoxLayout()

        # Timecode box with green border by default
        self.timecode_frame = QFrame()
        self.timecode_frame.setStyleSheet("""
            QFrame {
                border: 2px solid #4CAF50;  /* Green border by default */
                border-radius: 10px;
                background-color: #222222;
                padding: 10px;
            }
        """)
        self.timecode_frame_layout = QVBoxLayout()
        self.timecode_display = QLabel("00:00:00:00")
        self.timecode_display.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        self.timecode_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timecode_frame_layout.addWidget(self.timecode_display)
        self.timecode_frame.setLayout(self.timecode_frame_layout)
        self.input_layout.addWidget(self.timecode_frame)

        self.input_list_label = QLabel("Available Inputs:")
        self.input_list_label.setStyleSheet("font-weight: bold;")
        self.input_layout.addWidget(self.input_list_label)

        self.input_list = QListWidget()
        self.input_layout.addWidget(self.input_list)

        self.input_groupbox.setLayout(self.input_layout)

        self.right_layout = QVBoxLayout()

        self.ip_label = QLabel("ATEM IP:")
        self.ip_input = QLineEdit()
        self.right_layout.addWidget(self.ip_label)
        self.right_layout.addWidget(self.ip_input)

        self.hyperdeck_ip_label = QLabel("HyperDeck IP:")
        self.hyperdeck_ip_input = QLineEdit()
        self.right_layout.addWidget(self.hyperdeck_ip_label)
        self.right_layout.addWidget(self.hyperdeck_ip_input)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_to_atem)
        self.right_layout.addWidget(self.connect_button)

        self.frames_label = QLabel("Frame Compensation:")
        self.frames_input = QLineEdit("2")  # Default value is 2 frames
        self.frames_input.setEnabled(False)  # Disable by default
        self.right_layout.addWidget(self.frames_label)
        self.right_layout.addWidget(self.frames_input)

        # Checkbox to enable/disable frame compensation
        self.compensation_checkbox = QCheckBox("Enable frame compensation")
        self.compensation_checkbox.clicked.connect(self.toggle_frames_input)
        self.right_layout.addWidget(self.compensation_checkbox)

        self.current_input_label = QLabel("Selected input: Not defined")
        self.right_layout.addWidget(self.current_input_label)

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.toggle_monitoring)

        self.save_button = QPushButton("Choose EDL save location")
        self.save_button.clicked.connect(self.choose_save_location)

        self.log_table = QTableWidget(0, 3)
        self.log_table.setHorizontalHeaderLabels(["Source", "Start", "End"])
        self.right_layout.addWidget(self.log_table)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.start_button)
        self.right_layout.addLayout(button_layout)

        self.layout.addWidget(self.input_groupbox, 30)
        self.layout.addLayout(self.right_layout, 70)

        self.setLayout(self.layout)

        self.monitor_thread = None
        self.file_path = None
        self.is_monitoring = False
        self.atem = ATEMMax()
        self.stop_event = threading.Event()
        self.last_program_input = None

    def connect_to_atem(self):
        atem_ip = self.ip_input.text()
        if not self.is_valid_ip(atem_ip):
            self.show_error("Invalid IP Address", "The entered IP address is incorrect or unreachable. Please verify the IP and try again.")
            return

        hyperdeck_ip = self.hyperdeck_ip_input.text()
        if not self.is_valid_ip(hyperdeck_ip):
            self.show_error("Invalid HyperDeck IP Address", "The HyperDeck IP address is incorrect. Please verify the IP and try again.")
            return

        try:
            self.atem.connect(atem_ip)

            inputs = []
            for video_source_name in dir(self.atem.atem.videoSources):
                if not video_source_name.startswith("__"):
                    if video_source_name.lower().startswith('input'):
                        inputs.append(video_source_name)

            self.input_list.clear()
            self.input_list.addItems(inputs)

        except Exception as e:
            self.show_error("Connection Error", str(e))

    def toggle_frames_input(self):
        """
        Enables or disables the frame compensation input field
        based on the checkbox state.
        """
        if self.compensation_checkbox.isChecked():
            self.frames_input.setEnabled(True)
        else:
            self.frames_input.setEnabled(False)

    def toggle_monitoring(self):
        if self.is_monitoring:
            self.stop_event.set()
            self.monitor_thread.wait()
            self.is_monitoring = False
            self.start_button.setText("Start")
            # Revert to green border
            self.timecode_frame.setStyleSheet("""
                QFrame {
                    border: 2px solid #4CAF50;
                    border-radius: 10px;
                    background-color: #222222;
                    padding: 10px;
                }
            """)
        else:
            self.stop_event.clear()
            start_time = time.time()
            compensation_frames = int(self.frames_input.text()) if self.compensation_checkbox.isChecked() else 0  # Get number of frames to add if enabled
            self.monitor_thread = ATEMMonitorThread(self.ip_input.text(),
                                                    self.hyperdeck_ip_input.text(),
                                                    self.stop_event,
                                                    start_time,
                                                    self.file_path,
                                                    compensation_frames)
            self.monitor_thread.update_input_signal.connect(self.update_current_input)
            self.monitor_thread.update_log_signal.connect(self.update_log_table)
            self.monitor_thread.update_timecode_signal.connect(self.update_timecode)
            self.monitor_thread.start()
            self.is_monitoring = True
            self.start_button.setText("Stop")

            # Red border when logging starts
            self.timecode_frame.setStyleSheet("""
                QFrame {
                    border: 2px solid red;
                    border-radius: 10px;
                    background-color: #222222;
                    padding: 10px;
                }
            """)

    def choose_save_location(self):
        options = QFileDialog.Option.ReadOnly
        file_path, _ = QFileDialog.getSaveFileName(self, "Choose Save Location", "", "EDL Files (*.edl)", options=options)
        if file_path:
            self.file_path = file_path
            log.info(f"EDL file save location: {file_path}")

    def update_current_input(self, input_str):
        self.current_input_label.setText(f"Selected input: {input_str}")

        for i in range(self.input_list.count()):
            item = self.input_list.item(i)
            if item.text() == input_str:
                item.setBackground(QColor(255, 0, 0))
            else:
                item.setBackground(QColor(0, 0, 0))

    def update_log_table(self, src, start, duration):
        row_position = self.log_table.rowCount()
        self.log_table.insertRow(row_position)
        self.log_table.setItem(row_position, 0, QTableWidgetItem(src))
        self.log_table.setItem(row_position, 1, QTableWidgetItem(start))
        self.log_table.setItem(row_position, 2, QTableWidgetItem(duration))

    def update_timecode(self, timecode):
        # Display the timecode only
        self.timecode_display.setText(timecode)

    def is_valid_ip(self, ip):
        try:
            socket.inet_aton(ip)
            return True
        except socket.error:
            return False

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event):
        """Handle closing the application properly"""
        if self.is_monitoring:
            self.stop_event.set()  # Stop monitoring
            self.monitor_thread.wait()  # Wait for the thread to finish

        # Close the application properly after all operations are complete
        event.accept()

if __name__ == "__main__":
    app = QApplication([])
    window = ATEMGUI()
    window.show()
    app.exec()
