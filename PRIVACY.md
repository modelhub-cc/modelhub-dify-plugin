# Privacy Policy

This plugin sends your requests to ModelHub (`modelhub.cc`) so that a Seedance model can make the video you ask for.

## What is sent

- **To ModelHub (`https://modelhub.cc/v1`):** your prompt, the settings you choose (model, duration, resolution, aspect ratio, sound), any reference images, videos or audio and first/last frames you attach (uploaded as files) or link to, and your ModelHub API key in the `Authorization` header to identify your account.
- ModelHub forwards the request to the upstream video provider that serves the chosen model. ModelHub's own handling of your data is described on [modelhub.cc](https://modelhub.cc).

## Other requests the plugin makes

- It downloads the finished video from the storage link in ModelHub's reply (`media.modelhub.cc`). This request carries no API key and no user data.
- To read the files you attach, the plugin fetches them from your own Dify instance through the Dify plugin SDK.

## What the plugin stores or logs

Nothing. The finished video is handed straight to Dify, which stores it as a file in your workspace. The plugin keeps no copy, does not log prompts, images or keys, and collects no analytics. Your API key is stored by Dify's own credential system, and the plugin never includes it in error messages.

## Personal data

Do not upload images of people without their consent. The plugin does not process health, financial, biometric or other sensitive data on purpose, but anything you put in a prompt or image is sent to ModelHub as described above.

## Contact

Questions about this policy: lakers202601@163.com, or open an issue at https://github.com/modelhub-cc/modelhub-dify-plugin/issues.
