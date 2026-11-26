from dify_plugin import DifyPluginEnv, Plugin

# A video takes from one to several minutes. Generate Video waits up to 8 minutes,
# then hands back a task ID for Get Video Task, so requests stay under this limit.
plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=600))

if __name__ == "__main__":
    plugin.run()
