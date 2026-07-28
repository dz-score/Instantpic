idf_component_register(SRCS "transport_common.c" "transport_http.c" "transport_uart.c"
                    INCLUDE_DIRS "include"
                    # `render` is for the dev-build /state and /frame endpoints
                    # only, and is read-only — commands still reach the render
                    # task exclusively through the queue.
                    REQUIRES protocol render
                    # esp_driver_uart rather than the `driver` umbrella, which
                    # would drag in every peripheral and defeat MINIMAL_BUILD.
                    PRIV_REQUIRES esp_http_server esp_driver_uart esp_timer)
