# Requirements must be UNCONDITIONAL.
#
# Component requirements are collected during ESP-IDF's early-expansion pass,
# which runs before sdkconfig is loaded, so CONFIG_* symbols are all empty here.
# (They do work for SRCS, which is only needed in the full pass.) Gating the
# networking requirements on CONFIG_LED_NODE_TRANSPORT_HTTP silently dropped
# them and main.c failed to find esp_event.h.
#
# main no longer needs any networking components at all: the HTTP transport
# owns its own WiFi setup, so nothing here changes between the dev and booth
# builds.
idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES nvs_flash protocol transport render)
