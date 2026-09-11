"""Local report labels; protocol data and source text remain Unicode throughout."""

LABELS = {
    'ru': {
        'Executive summary': 'Краткий обзор', 'Summary': 'Обзор', 'Decisions': 'Принятые решения',
        'Topics and key points': 'Темы и основные тезисы', 'Open questions': 'Открытые вопросы',
        'Risks': 'Риски', 'Action items': 'Поручения', 'Assignee': 'Ответственный',
        'Task': 'Задача', 'Deadline': 'Срок', 'Priority': 'Приоритет', 'Evidence references': 'Источники',
        'Transcript': 'Расшифровка', 'Meeting date: ': 'Дата встречи: ', 'Timezone: ': 'Часовой пояс: ',
        'Report needs review': 'Требуется проверка отчёта', 'Transcription needs review': 'Требуется проверка расшифровки',
        'No structured meeting findings were extracted. This is not a complete meeting protocol. Check the transcript and original recording before relying on this report.': 'Структурированные итоги не извлечены. Проверьте расшифровку и исходную запись.',
        'No source-checked summary is available.': 'Обзор, подтверждённый расшифровкой, не сформирован.',
        'No items were extracted for this section.': 'Сведения для этого раздела не найдены.',
        'No action items were extracted. Owners and deadlines have not been invented.': 'Поручения не найдены. Ответственные и сроки не добавлены без основания.',
        'Speech recognition output for review. It may contain errors; timestamps refer to the original recording.': 'Автоматическая расшифровка может содержать ошибки. Временные метки относятся к исходной записи.',
        'No speech was recognized. Check the recording and input audio source.': 'Речь не распознана. Проверьте запись и источник звука.',
        'No verified source excerpt': 'Подтверждённый фрагмент отсутствует', 'No references': 'Нет ссылок на источник',
        '[Check audio] ': '[Проверьте аудио] ', '[Needs review] ': '[Требуется проверка] ', '[Rejected] ': '[Отклонено] ',
        'Some cited passages have uncertain recognition. Check the recording for names and numbers.': 'Некоторые фрагменты распознаны неуверенно. Сверьте имена и числа с записью.',
        'low': 'Низкий', 'medium': 'Средний', 'high': 'Высокий', 'urgent': 'Срочный', 'not_specified': 'Не указан',
    },
    'kk': {
        'Executive summary': 'Қысқаша шолу', 'Summary': 'Шолу', 'Decisions': 'Қабылданған шешімдер',
        'Topics and key points': 'Тақырыптар мен негізгі ойлар', 'Open questions': 'Ашық сұрақтар',
        'Risks': 'Тәуекелдер', 'Action items': 'Тапсырмалар', 'Assignee': 'Жауапты адам',
        'Task': 'Тапсырма', 'Deadline': 'Мерзім', 'Priority': 'Басымдық', 'Evidence references': 'Дереккөздер',
        'Transcript': 'Транскрипт', 'Meeting date: ': 'Кездесу күні: ', 'Timezone: ': 'Уақыт белдеуі: ',
        'Report needs review': 'Есепті тексеру қажет', 'Transcription needs review': 'Транскриптті тексеру қажет',
        'No structured meeting findings were extracted. This is not a complete meeting protocol. Check the transcript and original recording before relying on this report.': 'Кездесудің құрылымдалған қорытындысы алынбады. Транскрипт пен түпнұсқа жазбаны тексеріңіз.',
        'No source-checked summary is available.': 'Транскриптпен расталған шолу жасалмады.',
        'No items were extracted for this section.': 'Бұл бөлімге қатысты мәлімет табылмады.',
        'No action items were extracted. Owners and deadlines have not been invented.': 'Тапсырмалар табылмады. Жауапты адамдар мен мерзімдер негізсіз қосылған жоқ.',
        'Speech recognition output for review. It may contain errors; timestamps refer to the original recording.': 'Автоматты транскриптте қателер болуы мүмкін. Уақыт белгілері түпнұсқа жазбаға сәйкес келеді.',
        'No speech was recognized. Check the recording and input audio source.': 'Сөйлеу танылмады. Жазбаны және дыбыс көзін тексеріңіз.',
        'No verified source excerpt': 'Расталған үзінді жоқ', 'No references': 'Дереккөз сілтемелері жоқ',
        '[Check audio] ': '[Аудионы тексеріңіз] ', '[Needs review] ': '[Тексеру қажет] ', '[Rejected] ': '[Қабылданбады] ',
        'Some cited passages have uncertain recognition. Check the recording for names and numbers.': 'Кейбір үзінділер сенімсіз танылған. Есімдер мен сандарды жазбамен салыстырыңыз.',
        'low': 'Төмен', 'medium': 'Орташа', 'high': 'Жоғары', 'urgent': 'Шұғыл', 'not_specified': 'Көрсетілмеген',
    },
}


def translator(language):
    labels = LABELS.get(language, {})
    return lambda text: labels.get(text, text)
