set(requires nvs_flash protocol transport render)

if(CONFIG_LED_NODE_TRANSPORT_HTTP)
    list(APPEND requires esp_netif esp_wifi esp_eth protocol_examples_common)
endif()

idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES ${requires})
