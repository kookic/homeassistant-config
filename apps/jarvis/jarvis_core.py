import appdaemon.plugins.hass.hassapi as hass
import ast
import sys
import subprocess
from subprocess import Popen, PIPE, STDOUT
import random
import string
import uuid
import json
import yaml
import requests
from pathlib import Path
import os, re, time
import paho.mqtt.publish as publish
from fuzzywuzzy import fuzz, process

######################
# Jarvis Core
######################

class jarvis_core(hass.Hass):

    def initialize(self):
        self.snips_mqtt_host = self.config.get('snips_mqtt_host')
        self.snips_mqtt_port = self.config.get('snips_mqtt_port', 1883)

        self.sessionId = ''
        self.siteId = 'default'

        self.global_vars['intents'] = {}

        self.speech_file = self.args.get('speech_file',
                                         '/data/speech_en.yml')

        self.players = []
        for player in self.args.get('media_player'):
            self.players.append(player)
        for player in self.players:
            self.log("__function__ (player): %s" % player['player'], 'DEBUG')
            player['volume'] = self.get_state(
                                            entity=player['player'],
                                            attribute='volume_level')

        self.listen_event(self.jarvis_mqtt, 'JARVIS_MQTT')
        self.listen_event(self.jarvis_notify, "JARVIS_NOTIFY")
        self.listen_event(self.jarvis_action, "JARVIS_ACTION")

        self.listen_event(self.jarvis_intent_not_recognized,
                          'JARVIS_INTENT_NOT_RECOGNIZED')


    def jarvis_register_intent(self, intent, callback):
        self.global_vars['intents'][intent] = callback

    def jarvis_mqtt(self, event_name, data, *args, **kwargs):
        self.log("__function__ %s" % data, 'INFO')

        if 'hermes/hotword/' in data.get('topic', ''):
            hotword_data = {'toggle': data['topic'].split('toggle')[-1],
                            'payload': data.get('payload')}
            self.jarvis_listening(hotword_data)

        if 'hermes/intent/' in data.get('topic', ''):
            if data.get('payload'):
                data = json.loads(data['payload'])
            if data['intent']['intentName'].startswith('user_'):
                intent = data['intent']['intentName'].split('__')[-1]
            else:
                intent = data['intent']['intentName'].split(':')[-1]
            self.log("__function__ intent: %s" % intent, 'INFO')
            if self.global_vars['intents'].get(intent):
                self.global_vars['intents'][intent](data)

    def jarvis_intent_not_recognized(self, event_name, data, *args, **kwargs):
        self.log("__function__: %s" %data, 'INFO')
        self.jarvis_notify('NONE',
                           {'text':
                            self.jarvis_get_speech('intent_not_recognized')})

    def jarvis_notify(self, data, *args, **kwargs):
        self.log("__function__: %s" % data, 'DEBUG')
        payload={'siteId': data.get('siteId', self.siteId),
                 'init': {'type': 'notification',
                          'text': data.get('text', '')}}
        publish.single('hermes/dialogueManager/startSession',
                       payload=json.dumps(payload),
                       hostname=self.snips_mqtt_host,
                       port=self.snips_mqtt_port)

    def jarvis_end_session(self, data, *args, **kwargs):
        self.log("__function__: %s" % data, 'DEBUG')
        payload={'sessionId': data.get('sessionId', ''),
                          'text': data.get('text', '')}
        publish.single('hermes/dialogueManager/endSession',
                       payload=json.dumps(payload),
                       hostname=self.snips_mqtt_host,
                       port=self.snips_mqtt_port)

    def jarvis_continue_session(self, data, *args, **kwargs):
        self.log("__function__: %s" % data, 'DEBUG')
        payload={'siteId': data.get('sessionId', ''),
                          'text': data.get('text', ''),
                          'intentFilter': data.get('intentFilter', [])}
        publish.single('hermes/dialogueManager/continueSession',
                       payload=json.dumps(payload),
                       hostname=self.snips_mqtt_host,
                       port=self.snips_mqtt_port)

    def jarvis_action(self, event_name, data, *args, **kwargs):
        self.log("__function__: %s" % data, 'DEBUG')
        payload={'siteId': data.get('siteId', self.siteId),
                 'customData': data.get('custom_data', 'default'),
                 'init': {'type': 'action',
                 'text': data.get('text', ''),
                 'canBeEnqueued': True,
                 'intentFilter': [data.get('intentFilter',
                                           'TSchmidty:YesNoResponse')]}}
        publish.single('hermes/dialogueManager/startSession',
                       payload=json.dumps(payload),
                       hostname=self.snips_mqtt_host,
                       port=self.snips_mqtt_port )

    def jarvis_yesno_response(self, data, *args, **kwargs):
        """Custom handler for specific Yes/No Intent.
           This pulls information from the customData
           To fire an intent."""
        self.log("__function__: %s" % data, "INFO")
        if data.get('payload'):
            data = json.loads(data.get('payload', data))

        intent = ''
        slots = []
        if data['slots'][0]['value']['value'] == 'yes':
            intent_data = ast.literal_eval(data.get('customData', ''))
            payload={'siteId': data.get('siteId', self.siteId),
                     'sessionId': data.get('sessionId'),
                     'input': data.get('customData'),
                     'intent': {'intentName': intent_data.get('intent', '')},
                     'slots': intent_data.get('slots', [])}
            publish.single('hermes/intent/'+intent_data.get('intent', ''),
                           payload=json.dumps(payload),
                           hostname=self.snips_mqtt_host,
                           port=self.snips_mqtt_port,
                           protocol=mqtt.MQTTv311
            )
        else:
            self.jarvis_notify('NONE', {"text": self.jarvis_speech('ok')})

    def jarvis_get_speech(self, speech):
        # hack for now so we get any speech changes without reloading
        with open(self.speech_file, "r") as f:
          self.speech = yaml.load(f)
        return random.choice(self.speech.get(speech))

    def snips_start_session(self, payload):
        publish.single('hermes/dialogueManager/startSession',
          payload=json.dumps(payload),
          hostname=self.snips_mqtt_host,
          port=self.snips_mqtt_port,
          protocol=mqtt.MQTTv311
        )

    def jarvis_listening(self, data, *args, **kwargs):
        self.log("__function__: %s" % data, 'DEBUG')
        if not self.args.get('ramp_volume_on_hotword'):
            self.log("__function__: ramp_volume_on_hotword disabled", 'INFO')
            return
        for player in self.players:
            if player['room'] in data['payload']:
                if data.get('toggle') == 'Off':
                    self.log("__function__ lowering volume in room: %s"
                             % player['room'], 'INFO')
                    if not self.get_state(entity=player['player'],
                                          attribute="is_volume_muted"):
                        player['volume'] = self.get_state(
                            entity=player['player'], attribute='volume_level')
                        self.jarvis_ramp_volume(player['player'],
                                                player['volume'], 0.2)
                if data.get('toggle') == 'On':
                    if not self.get_state(entity=player['player'],
                                          attribute="is_volume_muted"):
                        self.jarvis_ramp_volume(player['player'], 0.2,
                                                player['volume'])

    def jarvis_ramp_volume(self, player, volume, dst_vol, *args, **kwargs):
        self.log("__function__: %s %s %s" % (player, volume, dst_vol),
            'DEBUG')
        if self.get_state(player) == 'off':
            return
        time_time = time.time
        start = time_time()
        period = .1
        count = 0
        steps = 7
        step = (volume - dst_vol) / steps
        while True:
          if count == steps: break
          if (time_time() - start) > period:
            count += 1
            start += period
            self.call_service("media_player/volume_set",
              entity_id = player,
              volume_level = str(volume - step * count))