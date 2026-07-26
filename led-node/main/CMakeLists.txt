# Requirements must be UNCONDITIONAL.
#
# Component requirements are collected during ESP-IDF's early-expansion pass,
# which runs before sdkconfig is loaded, so CONFIG_* symbols are all empty here.
# (They do work for SRCS, which is only needed in the full pass.) Gating the
# networking requirements on CONFIG_LED_NODE_TRANSPORT_HTTP silently dropped
# them and main.c failed to find esp_event.h.
#
# The cost is that the booth build still *builds* the networking components even
# though main.c compiles them out. That goes away when the UART transport is
# adopted for real and example_connect() is deleted outright — see
# Docs/LED_NODE_ARCHITECTURE.md, which already flags that it must not survive
# into the booth build.
idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES nvs_flash
                                  protocol
                                  transport
                                  render
                                  esp_event
                                  esp_netif
                                  esp_wifi
                                  esp_eth
                                  protocol_examples_common)
