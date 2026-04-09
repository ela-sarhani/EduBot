class ConversationManager:
    def __init__(self):
        self.history = []

    def add_message(self, role, content):
        self.history.append({
            "role": role,
            "content": content
        })

    def get_history(self):
        return self.history

    def format_for_prompt(self):
        formatted = ""
        for msg in self.history:
            formatted += f"{msg['role']}: {msg['content']}\n"
        return formatted