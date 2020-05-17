# Copyright (C) 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Sample that implements a gRPC client for the Google Assistant API."""
# constants

GPIO_LED = 4
GPIO_FORCE = 23
GSR_TIMEOUT = 3

# global var
SHOULD_ASSIST = False
import datetime
import threading
import RPi.GPIO as GPIO
import concurrent.futures
import json
import logging
import os
import os.path
import pathlib2 as pathlib
import sys
import time
import uuid

import speech_recognition as sr

import click
import grpc
import google.auth.transport.grpc
import google.auth.transport.requests
import google.oauth2.credentials

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(GPIO_LED, GPIO.OUT)
GPIO.setup(GPIO_FORCE, GPIO.IN)

import signal, os, sys

def sigint_handler(signum, frame):
    GPIO.output(GPIO_LED, False)
    sys.exit(1)

def sigusr2_handler(signum, frame):
    global SHOULD_ASSIST
    print('Receive SHOULD_ASSIST request.')
    SHOULD_ASSIST = True

def sigusr2_handler_disabled(signum,frame):
    pass

signal.signal(signal.SIGINT, sigint_handler)

from google.assistant.embedded.v1alpha2 import (
    embedded_assistant_pb2,
    embedded_assistant_pb2_grpc
)
from tenacity import retry, stop_after_attempt, retry_if_exception

try:
    from . import (
        assistant_helpers,
        audio_helpers,
        browser_helpers,
        device_helpers
    )
except (SystemError, ImportError):
    import assistant_helpers
    import audio_helpers
    import browser_helpers
    import device_helpers


ASSISTANT_API_ENDPOINT = 'embeddedassistant.googleapis.com'
END_OF_UTTERANCE = embedded_assistant_pb2.AssistResponse.END_OF_UTTERANCE
DIALOG_FOLLOW_ON = embedded_assistant_pb2.DialogStateOut.DIALOG_FOLLOW_ON
CLOSE_MICROPHONE = embedded_assistant_pb2.DialogStateOut.CLOSE_MICROPHONE
PLAYING = embedded_assistant_pb2.ScreenOutConfig.PLAYING
DEFAULT_GRPC_DEADLINE = 60 * 3 + 5


class SampleAssistant(object):
    def __init__(self, language_code, device_model_id, device_id,
                 conversation_stream, display,
                 channel, deadline_sec, device_handler):
        self.language_code = language_code
        self.device_model_id = device_model_id
        self.device_id = device_id
        self.conversation_stream = conversation_stream
        self.display = display
        self.conversation_state = None
        self.is_new_conversation = True

        self.assistant = embedded_assistant_pb2_grpc.EmbeddedAssistantStub(
            channel
        )
        self.deadline = deadline_sec

        self.device_handler = device_handler

    def __enter__(self):
        return self

    def __exit__(self, etype, e, traceback):
        if e:
            return False
        self.conversation_stream.close()

    def is_grpc_error_unavailable(e):
        is_grpc_error = isinstance(e, grpc.RpcError)
        if is_grpc_error and (e.code() == grpc.StatusCode.UNAVAILABLE):
            logging.error('grpc unavailable error: %s', e)
            return True
        return False

    @retry(reraise=True, stop=stop_after_attempt(3),
           retry=retry_if_exception(is_grpc_error_unavailable))
    def assist(self):
        continue_conversation = False
        device_actions_futures = []

        self.conversation_stream.start_recording()
        GPIO.output(GPIO_LED,True)
        logging.info('Recording audio request.')

        def iter_log_assist_requests():
            for c in self.gen_assist_requests():
                assistant_helpers.log_assist_request_without_audio(c)
                yield c
            logging.debug('Reached end of AssistRequest iteration.')

        for resp in self.assistant.Assist(iter_log_assist_requests(),
                                          self.deadline):
            assistant_helpers.log_assist_response_without_audio(resp)
            if resp.event_type == END_OF_UTTERANCE:
                logging.info('End of audio request detected.')
                logging.info('Stopping recording.')
                self.conversation_stream.stop_recording()
            if resp.speech_results:
                logging.info('Transcript of user request: "%s".',
                             ' '.join(r.transcript
                                      for r in resp.speech_results))
            if len(resp.audio_out.audio_data) > 0:
                if not self.conversation_stream.playing:
                    self.conversation_stream.stop_recording()
                    self.conversation_stream.start_playback()
                    logging.info('Playing assistant response.')
                self.conversation_stream.write(resp.audio_out.audio_data)
            if resp.dialog_state_out.conversation_state:
                conversation_state = resp.dialog_state_out.conversation_state
                logging.debug('Updating conversation state.')
                self.conversation_state = conversation_state
            if resp.dialog_state_out.volume_percentage != 0:
                volume_percentage = resp.dialog_state_out.volume_percentage
                logging.info('Setting volume to %s%%', volume_percentage)
                self.conversation_stream.volume_percentage = volume_percentage
            if resp.dialog_state_out.microphone_mode == DIALOG_FOLLOW_ON:
                continue_conversation = True
                logging.info('Expecting follow-on query from user.')
            elif resp.dialog_state_out.microphone_mode == CLOSE_MICROPHONE:
                continue_conversation = False
            if resp.device_action.device_request_json:
                device_request = json.loads(
                    resp.device_action.device_request_json
                )
                fs = self.device_handler(device_request)
                if fs:
                    device_actions_futures.extend(fs)
            if self.display and resp.screen_out.data:
                system_browser = browser_helpers.system_browser
                system_browser.display(resp.screen_out.data)

        if len(device_actions_futures):
            logging.info('Waiting for device executions to complete.')
            concurrent.futures.wait(device_actions_futures)

        logging.info('Finished playing assistant response.')
        self.conversation_stream.stop_playback()
        GPIO.output(GPIO_LED,False)
        return continue_conversation

    def gen_assist_requests(self):
        """Yields: AssistRequest messages to send to the API."""

        config = embedded_assistant_pb2.AssistConfig(
            audio_in_config=embedded_assistant_pb2.AudioInConfig(
                encoding='LINEAR16',
                sample_rate_hertz=self.conversation_stream.sample_rate,
            ),
            audio_out_config=embedded_assistant_pb2.AudioOutConfig(
                encoding='LINEAR16',
                sample_rate_hertz=self.conversation_stream.sample_rate,
                volume_percentage=self.conversation_stream.volume_percentage,
            ),
            dialog_state_in=embedded_assistant_pb2.DialogStateIn(
                language_code=self.language_code,
                conversation_state=self.conversation_state,
                is_new_conversation=self.is_new_conversation,
            ),
            device_config=embedded_assistant_pb2.DeviceConfig(
                device_id=self.device_id,
                device_model_id=self.device_model_id,
            )
        )
        if self.display:
            config.screen_out_config.screen_mode = PLAYING
        # Continue current conversation with later requests.
        self.is_new_conversation = False
        # The first AssistRequest must contain the AssistConfig
        # and no audio data.
        yield embedded_assistant_pb2.AssistRequest(config=config)
        for data in self.conversation_stream:
            # Subsequent requests need audio data, but not config.
            yield embedded_assistant_pb2.AssistRequest(audio_in=data)

def create_credentials_and_http_request(credentials):
    try:
        with open(credentials, 'r') as f:
            credentials = google.oauth2.credentials.Credentials(token=None,
                                                                **json.load(f))
            http_request = google.auth.transport.requests.Request()
            credentials.refresh(http_request)
            return (credentials,http_request)
    except Exception as e:
        logging.error('Error loading credentials: %s', e)
        logging.error('Run google-oauthlib-tool to initialize '
                      'new OAuth 2.0 credentials.')
        sys.exit(-1)

def configure_device_id_and_model_id(device_config):
     try:
         with open(device_config) as f:
             device = json.load(f)
             device_id = device['id']
             device_model_id = device['model_id']
             logging.info("Using device model %s and device id %s",
                          device_model_id,
                          device_id)
             return device_id, device_model_id
     except Exception as e:
         logging.warning('Device config not found: %s' % e)
         logging.info('Registering device')
         if not device_model_id:
             logging.error('Option --device-model-id required '
                           'when registering a device instance.')
             sys.exit(-1)
         if not project_id:
             logging.error('Option --project-id required '
                           'when registering a device instance.')
             sys.exit(-1)
         device_base_url = (
             'https://%s/v1alpha2/projects/%s/devices' % (api_endpoint,
                                                          project_id)
         )
         device_id = str(uuid.uuid1())
         payload = {
             'id': device_id,
             'model_id': device_model_id,
             'client_type': 'SDK_SERVICE'
         }
         session = google.auth.transport.requests.AuthorizedSession(
             credentials
         )
         r = session.post(device_base_url, data=json.dumps(payload))
         if r.status_code != 200:
             logging.error('Failed to register device: %s', r.text)
             sys.exit(-1)
         logging.info('Device registered: %s', device_id)
         pathlib.Path(os.path.dirname(device_config)).mkdir(exist_ok=True)
         with open(device_config, 'w') as f:
             json.dump(payload, f)
         return device_id, device_model_id

def wait_for_okay_google():
     r = sr.Recognizer()
     with sr.Microphone() as source:
         print('[GSR][{}] Listening...'.format(datetime.datetime.now().isoformat()))
         try:
             audio = r.listen(source, phrase_time_limit=GSR_TIMEOUT)
             result = r.recognize_google(audio)
             print("[GSR] Heard: " + result)
             if ('OK Google' in result):
                return True
             return False
         except sr.UnknownValueError:
             print("[GSR] Could not understand.")
             return False
         except SystemExit:
             sys.exit(1)
         except:
             print('[GSR] Unknown Error: {}'.format(sys.exc_info()[0]))
             return False

@click.command()
@click.option('--api-endpoint', default=ASSISTANT_API_ENDPOINT,
              metavar='<api endpoint>', show_default=True,
              help='Address of Google Assistant API service.')
@click.option('--credentials',
              metavar='<credentials>', show_default=True,
              default=os.path.join(click.get_app_dir('google-oauthlib-tool'),
                                   'credentials.json'),
              help='Path to read OAuth2 credentials.')
@click.option('--project-id',
              metavar='<project id>',
              help=('Google Developer Project ID used for registration '
                    'if --device-id is not specified'))
@click.option('--device-model-id',
              metavar='<device model id>',
              help=(('Unique device model identifier, '
                     'if not specifed, it is read from --device-config')))
@click.option('--device-id',
              metavar='<device id>',
              help=(('Unique registered device instance identifier, '
                     'if not specified, it is read from --device-config, '
                     'if no device_config found: a new device is registered '
                     'using a unique id and a new device config is saved')))
@click.option('--device-config', show_default=True,
              metavar='<device config>',
              default=os.path.join(
                  click.get_app_dir('googlesamples-assistant'),
                  'device_config.json'),
              help='Path to save and restore the device configuration')
@click.option('--lang', show_default=True,
              metavar='<language code>',
              default='en-US',
              help='Language code of the Assistant')
@click.option('--display', is_flag=True, default=False,
              help='Enable visual display of Assistant responses in HTML.')
@click.option('--verbose', '-v', is_flag=True, default=False,
              help='Verbose logging.')
@click.option('--input-audio-file', '-i',
              metavar='<input file>',
              help='Path to input audio file. '
              'If missing, uses audio capture')
@click.option('--output-audio-file', '-o',
              metavar='<output file>',
              help='Path to output audio file. '
              'If missing, uses audio playback')
@click.option('--audio-sample-rate',
              default=audio_helpers.DEFAULT_AUDIO_SAMPLE_RATE,
              metavar='<audio sample rate>', show_default=True,
              help='Audio sample rate in hertz.')
@click.option('--audio-sample-width',
              default=audio_helpers.DEFAULT_AUDIO_SAMPLE_WIDTH,
              metavar='<audio sample width>', show_default=True,
              help='Audio sample width in bytes.')
@click.option('--audio-iter-size',
              default=audio_helpers.DEFAULT_AUDIO_ITER_SIZE,
              metavar='<audio iter size>', show_default=True,
              help='Size of each read during audio stream iteration in bytes.')
@click.option('--audio-block-size',
              default=audio_helpers.DEFAULT_AUDIO_DEVICE_BLOCK_SIZE,
              metavar='<audio block size>', show_default=True,
              help=('Block size in bytes for each audio device '
                    'read and write operation.'))
@click.option('--audio-flush-size',
              default=audio_helpers.DEFAULT_AUDIO_DEVICE_FLUSH_SIZE,
              metavar='<audio flush size>', show_default=True,
              help=('Size of silence data in bytes written '
                    'during flush operation'))
@click.option('--grpc-deadline', default=DEFAULT_GRPC_DEADLINE,
              metavar='<grpc deadline>', show_default=True,
              help='gRPC deadline in seconds')
@click.option('--once', default=False, is_flag=True,
              help='Force termination after a single conversation.')
def main(api_endpoint, credentials, project_id,
         device_model_id, device_id, device_config,
         lang, display, verbose,
         input_audio_file, output_audio_file,
         audio_sample_rate, audio_sample_width,
         audio_iter_size, audio_block_size, audio_flush_size,
         grpc_deadline, once, *args, **kwargs):
    # Setup logging.
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)

    # Load OAuth 2.0 credentials.
    credentials, http_request = create_credentials_and_http_request(credentials)

    # Create an authorized gRPC channel.
    grpc_channel = google.auth.transport.grpc.secure_authorized_channel(
        credentials, http_request, api_endpoint)
    logging.info('Connecting to %s', api_endpoint)

    # Configure audio source and sink.
    audio_device = None
    audio_source = audio_device = (
        audio_device or audio_helpers.SoundDeviceStream(
            sample_rate=audio_sample_rate,
            sample_width=audio_sample_width,
            block_size=audio_block_size,
            flush_size=audio_flush_size
        )
    )

    audio_sink = audio_device = (
            audio_device or audio_helpers.SoundDeviceStream(
                sample_rate=audio_sample_rate,
                sample_width=audio_sample_width,
                block_size=audio_block_size,
                flush_size=audio_flush_size
            )
        )
        # Create conversation stream with the given audio source and sink.
    conversation_stream = audio_helpers.ConversationStream(
        source=audio_source,
        sink=audio_sink,
        iter_size=audio_iter_size,
        sample_width=audio_sample_width,
    )

    if not device_id or not device_model_id:
        device_id, device_model_id = configure_device_id_and_model_id(device_config)

    device_handler = device_helpers.DeviceRequestHandler(device_id)

    with SampleAssistant(lang, device_model_id, device_id,
                         conversation_stream, display,
                         grpc_channel, grpc_deadline,
                         device_handler) as assistant:

        if input_audio_file or output_audio_file:
            assistant.assist()
            return

        while True:
            signal.signal(signal.SIGUSR2, sigusr2_handler)
            global SHOULD_ASSIST
            if not SHOULD_ASSIST:
                time.sleep(0.5)
                continue # keep looping
            # temp disabling sighandler
            signal.signal(signal.SIGUSR2, sigusr2_handler_disabled)
            continue_conversation = assistant.assist()
            if not continue_conversation:
                SHOULD_ASSIST = False
            # reenable them again

class listenButtonPress(threading.Thread):
   def __init__(self):
      threading.Thread.__init__(self)
   def run(self):
      while True:
          if GPIO.input(GPIO_FORCE):
              print('[BTN] button press triggered')
              os.kill(os.getpid(), signal.SIGUSR2)
          time.sleep(0.5)

class listenOKGoogle(threading.Thread):
   def __init__(self):
      threading.Thread.__init__(self)
   def run(self):
      while True:
          should_assist = wait_for_okay_google()
          if should_assist:
              print('[GSR] OK Google voice triggered')
              os.kill(os.getpid(), signal.SIGUSR2)

if __name__ == '__main__':
    button_thread = listenButtonPress()
    ok_thread = listenOKGoogle()
    button_thread.start()
    ok_thread.start()
    main()
