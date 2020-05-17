# Google Assistant on Pi
## Prerequisites
You will need to make sure the microphone and the speaker on your Pi works. Follow [this tutorial](https://developers.google.com/assistant/sdk/guides/service/python/embed/audio) to configure and test the audio.

## Setup
- Follow [this tutorial to set up your GCP Project](https://developers.google.com/assistant/sdk/guides/service/python/embed/config-dev-project-and-account) and [this tutorial to register your device](https://developers.google.com/assistant/sdk/guides/service/python/embed/register-device). Be sure to save your json credential, and note down `your-gcp-project-id`, and `your-device-model-id`.
- Set up virtual env and clone the repo
```
sudo apt-get update
sudo apt-get install python3-dev python3-venv # Use python3.4-venv if the package cannot be found.
git clone <url for this repo>
cd <repo>
python3 -m venv env
env/bin/python -m pip install --upgrade pip setuptools wheel
source env/bin/activate
```
- Install the packages under the virtual env
```
sudo apt-get install portaudio19-dev libffi-dev libssl-dev
python3 -m pip install --upgrade google-assistant-sdk[samples] google-auth-oauthlib[tool]
```
- Generate Credentials
```
google-oauthlib-tool --scope https://www.googleapis.com/auth/assistant-sdk-prototype \
      --save --headless --client-secrets /path/to/client_secret_client-id.json
```
- Follow the steps from Google and a credential file will be saved to `$HOME/.config/google-oauthlib-tool`.

## Configure GPIO PINs
- Edit `start.py` with your favorite editor.
- Update `GPIO_LED` to the GPIO output PIN that controls the LED you want to use to indicate the activation status of your Google Assistant.
- Update `GPIO_FORCE` to the GPIO input PIN that connects to the switch that acts like a push button to force activate Google Assistant.
- Update `GSR_TIMEOUT` to number of seconds you want the ever-running backend to sample "OK Google".

## First time running
When you run the program the first time, you will need to pass in the project id, and the device model ids.
```
source /env/bin/activate
python3 start.py --project-id my-dev-project --device-model-id my-model
```
Now try saying 'OK Google' or push the button to activate Google Assitant.

## Subsequent runs
The credentials and IDs are saved from your first run at `$HOME/.config/googlesamples-assistant/device_config.json`; therefore, you no longer need to pass them in in subsequent runs

```
source /env/bin/activate
python3 start.py
```

## Troubleshooting
TODO
