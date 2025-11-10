# hive-dynamic-boost-control-home-assistant
Smart automation for Hive thermostats in Home Assistant that dynamically limits Boost mode based on temperature — perfect for HMO or shared houses to prevent energy waste.

# Hive Dynamic Boost Automation for Home Assistant

This project provides a **dynamic temperature-based Boost control** for Hive thermostats integrated with **Home Assistant**.  
The automation adjusts Boost duration based on the current room temperature, automatically ending the cycle and logging every event to a local file.

---

## ⚙️ Features
- Automatically detects when Boost mode is activated (`preset_mode: boost`).
- Dynamically adjusts the Boost duration:
  | Temperature | Duration | Behavior |
  |--------------|-----------|-----------|
  | < 21°C       | 10 min    | Full heating cycle |
  | 21–22°C      | 7 min     | Shortened cycle |
  | 22–22.5°C    | 5 min     | Shorter cycle |
  | 22.5–23°C    | 3 min     | Minimal boost |
  | > 23°C       | 0 min     | Blocked (no Boost) |
- Automatically disables Boost after time elapses.
- Creates persistent notifications.
- Writes log entries to `/config/boost_history.log`.

---

## 🧠 How It Works
The automation listens for changes in `preset_mode` of the Hive thermostat.  
When Boost is enabled, it checks the current temperature and:
1. Calculates the proper Boost duration.
2. Keeps heating for the given period.
3. Logs the action to:
   - **System log**
   - **Home Assistant Logbook**
   - **Custom file** `/config/boost_history.log`

---

## 🧩 Example File Structure

### `configuration.yaml`
```yaml
shell_command:
  boost_history_log: >
    bash -c 'echo "$(date "+%Y-%m-%d %H:%M:%S") - {{ message }}" >> /config/boost_history.log'

