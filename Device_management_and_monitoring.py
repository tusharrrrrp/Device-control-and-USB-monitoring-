import os
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, simpledialog
import logging
import win32com.client
import ctypes
import cv2
import pyaudio
import threading
import psutil
import time
from queue import Queue

# Configure logging
logging.basicConfig(
    filename="device_control_logs.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Global variables
seen_microphones = {}
seen_cameras = {}
logged_apps = {"Microphone": set(), "Camera": set(), "General": set()}  # Tracks logged apps for each device
data_queue = Queue()  # Thread-safe queue for communication

# Flag to manage monitoring
monitoring_flag = False

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception as e:
        logging.error(f"Error checking admin rights: {e}")
        return False

def list_usb_devices():
    try:
        wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        conn = wmi.ConnectServer(".", "root\\cimv2")
        devices = conn.ExecQuery("SELECT * FROM Win32_PnPEntity WHERE PNPDeviceID LIKE '%USB%'")

        usb_devices = []
        for device in devices:
            name = device.Name
            device_id = device.PNPDeviceID
            usb_devices.append((name, device_id))

        return usb_devices
    except Exception as e:
        logging.error(f"Error listing USB devices: {e}")
        return []

def refresh_device_list():
    usb_devices = list_usb_devices()
    device_var.set("Select a device")
    menu = device_dropdown["menu"]
    menu.delete(0, "end")
    
    global device_map
    device_map = {name: device_id for name, device_id in usb_devices}
    for name in device_map.keys():
        menu.add_command(label=name, command=lambda value=name: device_var.set(value))

def toggle_device(device_name, enable):
    if not is_admin():
        messagebox.showerror("Error", "This script requires administrator privileges.")
        return

    device_id = device_map.get(device_name)
    if not device_id:
        messagebox.showerror("Error", "Invalid device selected.")
        return

    try:
        wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        conn = wmi.ConnectServer(".", "root\\cimv2")

        action = "enabled" if enable else "disabled"
        logging.info(f"Attempting to {action} device: {device_id}")

        try:
            method = "Enable" if enable else "Disable"
            conn.ExecMethod(f"Win32_PnPEntity.DeviceID='{device_id}'", method)
            logging.info(f"Successfully {action} device: {device_id}")
            messagebox.showinfo("Success", f"Device has been {action}!")
        except Exception as device_error:
            logging.error(f"Failed to {action} device: {device_id}. Error: {device_error}")
            raise

    except Exception as e:
        logging.error(f"Error toggling device: {e}")
        messagebox.showerror("Error", f"Failed to {'enable' if enable else 'disable'} device. {e}")

def view_logs():
    try:
        with open("device_control_logs.log", "r") as log_file:
            logs = log_file.read()
            log_window = tk.Toplevel(root)
            log_window.title("Logs")
            log_text = scrolledtext.ScrolledText(log_window, width=60, height=20)
            log_text.pack(padx=10, pady=10)
            log_text.insert(tk.END, logs)
            log_text.config(state=tk.DISABLED)
    except FileNotFoundError:
        messagebox.showinfo("Logs", "No logs available yet.")

def save_log_file():
    filename = simpledialog.askstring("Save Log File", "Enter filename for the log (without extension):")
    if filename:
        desktop_path = os.path.join(os.path.join(os.environ['USERPROFILE']), 'Desktop')
        file_path = os.path.join(desktop_path, f"{filename}.log")

        try:
            with open("device_control_logs.log", "r") as log_file:
                logs = log_file.read()

            with open(file_path, "w") as new_log_file:
                new_log_file.write(logs)

            messagebox.showinfo("Success", f"Log file saved as {filename}.log on your Desktop!")
        except Exception as e:
            logging.error(f"Error saving log file: {e}")
            messagebox.showerror("Error", f"Failed to save log file. {e}")

def get_all_processes():
    """Function to get all active processes."""
    active_processes = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            active_processes.append(proc.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return active_processes

def monitor_microphone():
    audio = pyaudio.PyAudio()
    while monitoring_flag:
        try:
            for i in range(audio.get_device_count()):
                device_info = audio.get_device_info_by_index(i)
                if device_info.get("maxInputChannels") > 0:
                    mic_name = device_info['name']
                    if mic_name not in seen_microphones:
                        seen_microphones[mic_name] = "In Use"

                    active_processes = get_all_processes()
                    for proc in active_processes:
                        if proc['name'] not in logged_apps["Microphone"]:
                            logged_apps["Microphone"].add(proc['name'])
                            data_queue.put(("Microphone", mic_name, proc['name'], proc['pid']))
        except Exception as e:
            print(f"Error monitoring microphone: {e}")
        time.sleep(2)

def monitor_camera():
    while monitoring_flag:
        try:
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                cam_name = "Default Camera"
                if cam_name not in seen_cameras:
                    seen_cameras[cam_name] = "In Use"

                active_processes = get_all_processes()
                for proc in active_processes:
                    if proc['name'] not in logged_apps["Camera"]:
                        logged_apps["Camera"].add(proc['name'])
                        data_queue.put(("Camera", cam_name, proc['name'], proc['pid']))
            cap.release()
        except Exception as e:
            print(f"Error monitoring camera: {e}")
        time.sleep(1)

def monitor_all_processes():
    """Monitor all active processes."""
    while monitoring_flag:
        try:
            active_processes = get_all_processes()
            for proc in active_processes:
                if proc['name'] not in logged_apps["General"]:
                    logged_apps["General"].add(proc['name'])
                    data_queue.put(("General", "N/A", proc['name'], proc['pid']))
        except Exception as e:
            logging.error(f"Error monitoring processes: {e}")
        time.sleep(5)  # Sleep to reduce CPU usage

def process_queue():
    while not data_queue.empty():
        device_type, device_name, app_name, pid = data_queue.get()
        device_table.insert("", tk.END, values=(device_type, device_name, app_name, pid))
    root.after(500, process_queue)

def start_monitoring():
    global monitoring_flag
    monitoring_flag = True  # Start monitoring threads
    threading.Thread(target=monitor_microphone, daemon=True).start()
    threading.Thread(target=monitor_camera, daemon=True).start()
    threading.Thread(target=monitor_all_processes, daemon=True).start()  # Start process monitoring

def stop_monitoring():
    global monitoring_flag
    monitoring_flag = False  # Stop monitoring

# GUI setup
root = tk.Tk()
root.title("Device Control and Real-Time Monitor")
root.geometry("800x600")

# Create Notebook (Tabs)
notebook = ttk.Notebook(root)
notebook.pack(fill=tk.BOTH, expand=True)

# Tab 1: USB Device Control
device_control_tab = ttk.Frame(notebook)
notebook.add(device_control_tab, text="USB Device Control")

# Tab 2: Real-Time Monitoring
monitoring_tab = ttk.Frame(notebook)
notebook.add(monitoring_tab, text="Real-Time Monitoring")

# USB Device Control Tab
usb_devices = list_usb_devices()
device_var = tk.StringVar()
device_var.set("Select a device")

device_map = {name: device_id for name, device_id in usb_devices}
device_dropdown = tk.OptionMenu(device_control_tab, device_var, *device_map.keys())
device_dropdown.pack(pady=5)

btn_refresh = tk.Button(device_control_tab, text="Refresh Devices", command=refresh_device_list)
btn_refresh.pack(pady=5)

btn_disable = tk.Button(device_control_tab, text="Disable Device", command=lambda: toggle_device(device_var.get(), enable=False))
btn_disable.pack(pady=5)

btn_enable = tk.Button(device_control_tab, text="Enable Device", command=lambda: toggle_device(device_var.get(), enable=True))
btn_enable.pack(pady=5)

btn_logs = tk.Button(device_control_tab, text="View Logs", command=view_logs)
btn_logs.pack(pady=5)

btn_save_log = tk.Button(device_control_tab, text="Save Log File", command=save_log_file)
btn_save_log.pack(pady=5)

# Real-Time Monitoring Tab
frame = ttk.Frame(monitoring_tab)
frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

columns = ("Device Type", "Device Name", "Application Using It", "PID")
device_table = ttk.Treeview(frame, columns=columns, show="headings", yscrollcommand=scrollbar.set)
for col in columns:
    device_table.heading(col, text=col)
    device_table.column(col, width=150 if col != "PID" else 80)
device_table.pack(fill=tk.BOTH, expand=True)
scrollbar.config(command=device_table.yview)

start_button = ttk.Button(monitoring_tab, text="Start Monitoring", command=start_monitoring)
start_button.pack(pady=10)

stop_button = ttk.Button(monitoring_tab, text="Stop Monitoring", command=stop_monitoring)
stop_button.pack(pady=10)

root.after(100, process_queue)
root.mainloop()
