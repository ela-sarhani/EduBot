from agent import AIAgent
from lesson_controller import LessonController
from memory_manager import ConversationManager
from openrouter_client import OpenRouterClient

lesson = LessonController("lessons/GPIOs.json")
memory = ConversationManager()
ai = AIAgent(OpenRouterClient())

while True:
    section = lesson.get_current_section()

    # AI response
    response = ai.generate_response(section, memory.format_for_prompt())
    print("AI:", response)
    memory.add_message("assistant", response)

    # User input
    user_input = input("User: ")
    memory.add_message("user", user_input)

    # Evaluate understanding
    if ai.evaluate_understanding(section, memory.format_for_prompt()):
        print("Section completed !")
        lesson.move_next_section()

        if lesson.is_finished():
            print("Lesson completed !")
            break