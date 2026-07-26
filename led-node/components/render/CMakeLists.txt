# espressif/led_strip is declared in this component's idf_component.yml and is
# added to its requirements automatically by the component manager.
idf_component_register(SRCS "canvas.c"
                            "output.c"
                            "modes.c"
                            "anim_boot.c"
                            "anim_idle.c"
                            "anim_playful.c"
                            "anim_countdown.c"
                            "anim_capture.c"
                            "anim_printing.c"
                            "anim_finished.c"
                            "anim_error.c"
                            "anim_linklost.c"
                    INCLUDE_DIRS "include"
                    REQUIRES protocol
                    # No driver requirement of our own: led_strip declares
                    # esp_driver_rmt itself, and nothing here touches a
                    # peripheral directly.
                    PRIV_REQUIRES esp_timer)
