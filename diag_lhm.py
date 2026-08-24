#!/usr/bin/env python
"""Dump every CPU sensor LibreHardwareMonitor exposes. MUST run as administrator."""
import ctypes, os, sys, io

out = io.StringIO()
def say(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    out.write(line + "\n")

say("admin:", ctypes.windll.shell32.IsUserAnAdmin() != 0)
import clr
clr.AddReference(os.getcwd() + r'\external\LibreHardwareMonitor\LibreHardwareMonitorLib.dll')
clr.AddReference(os.getcwd() + r'\external\LibreHardwareMonitor\HidSharp.dll')
from LibreHardwareMonitor import Hardware

h = Hardware.Computer()
h.IsCpuEnabled = True
h.IsMotherboardEnabled = True
h.Open()

for hw in h.Hardware:
    if hw.HardwareType != Hardware.HardwareType.Cpu:
        continue
    hw.Update()
    for sub in hw.SubHardware:
        sub.Update()
    say(f"\nHARDWARE: {hw.Name}  ({hw.HardwareType})")
    say(f"{'SensorType':<16}{'Name':<34}Value")
    say("-" * 70)
    for s in hw.Sensors:
        say(f"{str(s.SensorType):<16}{str(s.Name):<34}{s.Value}")
    say("\n--- TEMPERATURE sensors only ---")
    for s in hw.Sensors:
        if s.SensorType == Hardware.SensorType.Temperature:
            say(f"  name={str(s.Name)!r:38} value={s.Value!r}")
h.Close()

with open("diag_lhm_output.txt", "w", encoding="utf-8") as f:
    f.write(out.getvalue())
say("\nwritten to diag_lhm_output.txt")
