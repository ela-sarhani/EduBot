# EduBot


## Overview

**EduBot** is an interactive smart learning companion designed to teach robotics through a combination of AI-guided instruction based on a specified curriculum, block-based programming and hands-on hardware interaction
The system bridges the gap between theory and practice by guiding users step-by-step through structured lessons, while allowing them to build and test real programs on embedded hardware.

---

## Project Goals

* Make robotics education accessible and interactive
* Replace passive learning with guided experimentation
* Provide real-time feedback and correction

---

## Learning System

### Lesson Structure

Lessons are defined in JSON and organized into:

* Sections (progressive concepts)
* Key points
* Questions for interaction
* Summary

Example topics:

* GPIO fundamentals
* Digital signals (HIGH / LOW)
* Input vs Output modes
* Electrical safety

---

### AI Teacher-Agent

The AI agent:

* Guides the user through the lessons
* Asks questions instead of giving direct answers
* Detects misunderstandings
* Provides hints and explanations
* Controls progression through the provided sections

---

### Conversation-Based Learning

* Interactive Q&A flow
* Adaptive explanations
* Section completion based on understanding
* Memory of previous interactions

---

## Block-Based Programming & Execution Engine

EduBot includes a simplified programming environment inspired by Scratch. The user can assemble the code in order to do a simple application of the theory learned. When checked to be a correct logic but the assistant, a corresponding firmware (.ino file) is then generated.

---

## Hardware Integration


---


## Team

Ela Sarhani
Fraj Abdelaziz

