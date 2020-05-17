# Google Assistant on Pi
## Setup and Prerequisites
- Follow [this tutorial to set up your GCP Project](https://developers.google.com/assistant/sdk/guides/service/python/embed/config-dev-project-and-account) and [this tutorial to register your device](https://developers.google.com/assistant/sdk/guides/service/python/embed/register-device). Be sure to save your json credential, and note down `your-gcp-project-id`, and `your-device-model-id`.
- Set up virtual env and clone the repo
```
sudo apt-get update
sudo apt-get install python3-dev python3-venv # Use python3.4-venv if the package cannot be found.
python3 -m venv env
env/bin/python -m pip install --upgrade pip setuptools wheel
source env/bin/activate
git clone <url for this repo>
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

## First time running
When you run the program the first time, you will need to pass in the project id, and the device model ids.
```
source /env/bin/activate
python3 start.py --project-id my-dev-project --device-model-id my-model
```

## Subsequent runs
The credentials and IDs are saved from your first run at `$HOME/.config/googlesamples-assistant/device_config.json`; therefore, you no longer need to pass them in in subsequent runs

```
source /env/bin/activate
python3 start.py
```

## Troubleshooting
TODO
