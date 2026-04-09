import json

class LessonController:
    def __init__(self, lesson_path):
        with open(lesson_path, "r") as f:
            self.lesson = json.load(f)

        self.current_section_index = 0

    def get_current_section(self):
        return self.lesson["sections"][self.current_section_index]

    def get_lesson_title(self):
        return self.lesson["title"]

    def move_next_section(self):
        if self.current_section_index < len(self.lesson["sections"]) - 1:
            self.current_section_index += 1
            return True
        return False

    def is_finished(self):
        return self.current_section_index >= len(self.lesson["sections"]) - 1

    def get_progress(self):
        return {
            "current": self.current_section_index + 1,
            "total": len(self.lesson["sections"])
        }