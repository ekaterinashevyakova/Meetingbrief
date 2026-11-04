import streamlit as st

st.set_page_config(page_title="MeetingBrief", page_icon="📋")

st.title("MeetingBrief")
st.write("Подготовьтесь к встрече с помощью краткой справки с источниками.")
st.info("Демонстрационный режим. Поиск и AI пока не подключены.")

with st.form("meeting_form"):
    entity_type = st.radio("О ком подготовить справку?", ["Компания", "Человек"])
    name = st.text_input("Название компании или имя человека")
    details = st.text_input("Сайт компании или должность — необязательно")
    meeting_date = st.date_input("Дата встречи")
    meeting_time = st.time_input("Время встречи")
    timezone = st.selectbox(
        "Часовой пояс",
        ["Europe/Rome", "Europe/Moscow", "UTC"],
    )
    goal = st.text_area("Цель встречи — необязательно")
    submitted = st.form_submit_button("Подготовить справку")

if submitted:
    if not name.strip():
        st.warning("Введите название компании или имя человека.")
    else:
        st.subheader(f"Подготовка к встрече: {name.strip()}")
        st.write(
            f"Встреча: {meeting_date:%d.%m.%Y}, "
            f"{meeting_time:%H:%M} · {timezone}"
        )
        st.caption("Ниже — пример структуры. Реальные сведения ещё не загружаются.")

        st.markdown("### Главное перед встречей")
        st.write("Здесь появится краткое резюме найденной информации.")

        st.markdown("### Недавние события")
        st.write("Здесь будут события с датами и ссылками на источники.")

        st.markdown("### Вопросы для встречи")
        st.write("Какие задачи сейчас наиболее важны для вашей команды?")

        st.markdown("### Что осталось неизвестным")
        st.write("Источники пока не исследованы — это демонстрация интерфейса.")