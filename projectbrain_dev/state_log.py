def log_state(user_input, response):
    with open("state_log.txt", "a") as f:
        f.write(f"{user_input} -> {response}\n")
