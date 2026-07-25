idf_component_register(SRCS "transport_common.c" "transport_http.c" "transport_uart.c"
                    INCLUDE_DIRS "include"
                    REQUIRES protocol
                    PRIV_REQUIRES esp_http_server driver esp_timer)
