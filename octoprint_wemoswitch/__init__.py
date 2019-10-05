# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.server import user_permission
import socket
import json
import logging
import os
import re
import threading
import time
import pywemo

class wemoswitchPlugin(octoprint.plugin.SettingsPlugin,
                            octoprint.plugin.AssetPlugin,
                            octoprint.plugin.TemplatePlugin,
							octoprint.plugin.SimpleApiPlugin,
							octoprint.plugin.StartupPlugin):

	def __init__(self):
		self._logger = logging.getLogger("octoprint.plugins.wemoswitch")
		self._wemoswitch_logger = logging.getLogger("octoprint.plugins.wemoswitch.debug")
		self.discovered_devices = []

	##~~ StartupPlugin mixin

	def on_startup(self, host, port):
		# setup customized logger
		from octoprint.logging.handlers import CleaningTimedRotatingFileHandler
		wemoswitch_logging_handler = CleaningTimedRotatingFileHandler(self._settings.get_plugin_logfile_path(postfix="debug"), when="D", backupCount=3)
		wemoswitch_logging_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
		wemoswitch_logging_handler.setLevel(logging.DEBUG)

		self._wemoswitch_logger.addHandler(wemoswitch_logging_handler)
		self._wemoswitch_logger.setLevel(logging.DEBUG if self._settings.get_boolean(["debug_logging"]) else logging.INFO)
		self._wemoswitch_logger.propagate = False

	def on_after_startup(self):
		self._logger.info("WemoSwitch loaded!")
		self.discovered_devices = pywemo.discover_devices()

	##~~ SettingsPlugin mixin

	def get_settings_defaults(self):
		return dict(
			debug_logging = False,
			arrSmartplugs = [{'ip':'','label':'','icon':'icon-bolt','displayWarning':True,'warnPrinting':False,'thermal_runaway':False,'gcodeEnabled':False,'gcodeOnDelay':1,'gcodeOffDelay':1,'autoConnect':True,'autoConnectDelay':10.0,'autoDisconnect':True,'autoDisconnectDelay':0,'sysCmdOn':False,'sysRunCmdOn':'','sysCmdOnDelay':0,'sysCmdOff':False,'sysRunCmdOff':'','sysCmdOffDelay':0,'currentState':'unknown','btnColor':'#808080'}],
			pollingInterval = 15,
			pollingEnabled = False,
			thermal_runaway_monitoring = False,
			thermal_runaway_max_bed = 0,
			thermal_runaway_max_extruder = 0
		)

	def on_settings_save(self, data):
		old_debug_logging = self._settings.get_boolean(["debug_logging"])

		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

		new_debug_logging = self._settings.get_boolean(["debug_logging"])
		if old_debug_logging != new_debug_logging:
			if new_debug_logging:
				self._wemoswitch_logger.setLevel(logging.DEBUG)
			else:
				self._wemoswitch_logger.setLevel(logging.INFO)

	def get_settings_version(self):
		return 2

	def on_settings_migrate(self, target, current=None):
		if current is None or current < 1:
			# Reset plug settings to defaults.
			self._logger.debug("Resetting arrSmartplugs for wemoswitch settings.")
			self._settings.set(['arrSmartplugs'], self.get_settings_defaults()["arrSmartplugs"])
		if current == 1:
			arrSmartplugs_new = []
			for plug in self._settings.get(['arrSmartplugs']):
				plug["thermal_runaway"] = False
				arrSmartplugs_new.append(plug)
			self._settings.set(["arrSmartplugs"],arrSmartplugs_new)

	##~~ AssetPlugin mixin

	def get_assets(self):
		return dict(
			js=["js/wemoswitch.js"],
			css=["css/wemoswitch.css"]
		)

	##~~ TemplatePlugin mixin

	def get_template_configs(self):
		return [
			dict(type="navbar", custom_bindings=True),
			dict(type="settings", custom_bindings=True)
		]

	##~~ SimpleApiPlugin mixin

	def turn_on(self, plugip):
		self._wemoswitch_logger.debug("Turning on %s." % plugip)
		plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
		self._wemoswitch_logger.debug(plug)
		chk = self.sendCommand("on",plugip)
		if chk == 0:
			self.check_status(plugip)
			if plug["autoConnect"]:
				t = threading.Timer(int(plug["autoConnectDelay"]),self._printer.connect)
				t.start()
			if plug["sysCmdOn"]:
				t = threading.Timer(int(plug["sysCmdOnDelay"]),os.system,args=[plug["sysRunCmdOn"]])
				t.start()

	def turn_off(self, plugip):
		self._wemoswitch_logger.debug("Turning off %s." % plugip)
		plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
		self._wemoswitch_logger.debug(plug)
		if plug["sysCmdOff"]:
			t = threading.Timer(int(plug["sysCmdOffDelay"]),os.system,args=[plug["sysRunCmdOff"]])
			t.start()
		if plug["autoDisconnect"]:
			self._printer.disconnect()
			time.sleep(int(plug["autoDisconnectDelay"]))
		chk = self.sendCommand("off",plugip)
		if chk == 0:
			self.check_status(plugip)

	def check_status(self, plugip):
		self._wemoswitch_logger.debug("Checking status of %s." % plugip)
		if plugip != "":
			chk = self.sendCommand("info",plugip)
			if chk == 1:
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="on",ip=plugip))
			elif chk == 8:
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="on",ip=plugip))
			elif chk == 0:
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="off",ip=plugip))
			else:
				self._wemoswitch_logger.debug(chk)
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="unknown",ip=plugip))

	def get_api_commands(self):
		return dict(turnOn=["ip"],turnOff=["ip"],checkStatus=["ip"])

	def on_api_command(self, command, data):
		if not user_permission.can():
			from flask import make_response
			return make_response("Insufficient rights", 403)
        
		if command == 'turnOn':
			self.turn_on("{ip}".format(**data))
		elif command == 'turnOff':
			self.turn_off("{ip}".format(**data))
		elif command == 'checkStatus':
			self.check_status("{ip}".format(**data))

	##~~ Utilities

	def plug_search(self, list, key, value): 
		for item in list: 
			if item[key] == value: 
				return item

	def sendCommand(self, cmd, plugip):
		# try to connect via ip address
		try:
			socket.inet_aton(plugip)
			ip = plugip
			self._wemoswitch_logger.debug("IP %s is valid." % plugip)
		except socket.error:
		# try to convert hostname to ip
			self._wemoswitch_logger.debug("Invalid ip %s trying hostname." % plugip)
			try:
				ip = socket.gethostbyname(plugip)
				self._wemoswitch_logger.debug("Hostname %s is valid." % plugip)
			except (socket.herror, socket.gaierror):
				self._wemoswitch_logger.debug("Invalid hostname %s." % plugip)
				return 3

		try:
			self._wemoswitch_logger.debug("Attempting to connect to %s" % plugip)
			port = pywemo.ouimeaux_device.probe_wemo(plugip)
			url = 'http://%s:%s/setup.xml' % (plugip, port)
			url = url.replace(':None','')
			self._wemoswitch_logger.debug("Getting device info from %s" % url)
			device = pywemo.discovery.device_from_description(url, None)

			self._wemoswitch_logger.debug("Found device %s" % device)
			self._wemoswitch_logger.debug("Sending command %s to %s" % (cmd,plugip))

			if cmd == "info":
				return device.get_state()
			elif cmd == "on":
				device.on()
				return 0
			elif cmd == "off":
				device.off()
				return 0

		except socket.error:
			self._wemoswitch_logger.debug("Could not connect to %s." % plugip)
			return 3

	##~~ Gcode processing hook

	def gcode_turn_off(self, plug):
		if plug["warnPrinting"] and self._printer.is_printing():
			self._logger.info("Not powering off %s because printer is printing." % plug["label"])
		else:
			self.turn_off(plug["ip"])

	def processGCODE(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		if gcode:
			if cmd.startswith("M80"):
				plugip = re.sub(r'^M80\s?', '', cmd)
				self._wemoswitch_logger.debug("Received M80 command, attempting power on of %s." % plugip)
				plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
				self._wemoswitch_logger.debug(plug)
				if plug["gcodeEnabled"]:
					t = threading.Timer(int(plug["gcodeOnDelay"]),self.turn_on,args=[plugip])
					t.start()
				return
			elif cmd.startswith("M81"):
				plugip = re.sub(r'^M81\s?', '', cmd)
				self._wemoswitch_logger.debug("Received M81 command, attempting power off of %s." % plugip)
				plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
				self._wemoswitch_logger.debug(plug)
				if plug["gcodeEnabled"]:
					t = threading.Timer(int(plug["gcodeOffDelay"]),self.gcode_turn_off,[plug])
					t.start()
				return
			else:
				return
		elif cmd.startswith("@WEMOON"):
			plugip = re.sub(r'^@WEMOON\s?', '', cmd)
			self._wemoswitch_logger.debug("Received @WEMOON command, attempting power on of %s." % plugip)
			plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
			self._wemoswitch_logger.debug(plug)
			if plug["gcodeEnabled"]:
				t = threading.Timer(int(plug["gcodeOnDelay"]),self.turn_on,args=[plugip])
				t.start()
			return None
		elif cmd.startswith("@WEMOOFF"):
			plugip = re.sub(r'^@WEMOOFF\s?', '', cmd)
			self._wemoswitch_logger.debug("Received @WEMOOFF command, attempting power off of %s." % plugip)
			plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
			self._wemoswitch_logger.debug(plug)
			if plug["gcodeEnabled"]:
				t = threading.Timer(int(plug["gcodeOffDelay"]),self.gcode_turn_off,[plug])
				t.start()
			return None

	def check_temps(self, parsed_temps):
		thermal_runaway_triggered = False
		for k, v in parsed_temps.items():
			if k == "B" and v[1] > 0 and v[0] > int(self._settings.get(["thermal_runaway_max_bed"])):
				self._wemoswitch_logger.debug("Max bed temp reached, shutting off plugs.")
				thermal_runaway_triggered = True
			if k.startswith("T") and v[1] > 0 and v[0] > int(self._settings.get(["thermal_runaway_max_extruder"])):
				self._wemoswitch_logger.debug("Extruder max temp reached, shutting off plugs.")
				thermal_runaway_triggered = True
			if thermal_runaway_triggered == True:
				for plug in self._settings.get(['arrSmartplugs']):
					if plug["thermal_runaway"] == True:
						response = self.turn_off(plug["ip"])
						if response["currentState"] == "off":
							self._plugin_manager.send_plugin_message(self._identifier, response)

	def monitor_temperatures(self, comm, parsed_temps):
		if self._settings.get(["thermal_runaway_monitoring"]):
			# Run inside it's own thread to prevent communication blocking
			t = threading.Timer(0,self.check_temps,[parsed_temps])
			t.start()
		return parsed_temps

	##~~ Softwareupdate hook

	def get_update_information(self):
		return dict(
			wemoswitch=dict(
				displayName="Wemo Switch",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="jneilliii",
				repo="OctoPrint-WemoSwitch",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/jneilliii/OctoPrint-WemoSwitch/archive/{target_version}.zip"
			)
		)

__plugin_name__ = "Wemo Switch"
__plugin_pythoncompat__ = ">=2.7,<4"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = wemoswitchPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.processGCODE,
		"octoprint.comm.protocol.temperatures.received": __plugin_implementation__.monitor_temperatures,
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}

