/* Learning Copilot review and submission screens.
 *
 * Plain DOM, no build step. Data from the server and from the AI is always
 * rendered with textContent; the only HTML inserted is the assignment brief,
 * which the server sanitises first.
 */
;(function () {
	'use strict'

	const root = document.getElementById('copilot')
	const CSRF = root.dataset.csrf
	const MESSAGES = window.COPILOT_MESSAGES || {}
	const state = { session: null, clockOffset: 0 }

	function __(text, args) {
		let result = MESSAGES[text] || text
		;(args || []).forEach((value, index) => {
			result = result.split('{' + index + '}').join(value)
		})
		return result
	}

	// ------------------------------------------------------------ DOM helpers

	function h(tag, attrs, ...children) {
		const el = document.createElement(tag)
		for (const [key, value] of Object.entries(attrs || {})) {
			if (value === null || value === undefined || value === false) continue
			if (key === 'class') el.className = value
			else if (key.startsWith('on')) el.addEventListener(key.slice(2), value)
			else if (key === 'dataset') Object.assign(el.dataset, value)
			else if (key === 'value') el.value = value
			else el.setAttribute(key, value === true ? '' : value)
		}
		append(el, children)
		return el
	}

	function append(el, children) {
		for (const child of children.flat(Infinity)) {
			if (child === null || child === undefined || child === false) continue
			el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)))
		}
		return el
	}

	function clear(el) {
		while (el.firstChild) el.removeChild(el.firstChild)
		return el
	}

	function link(path, ...children) {
		return h(
			'a',
			{
				href: '/copilot/' + path,
				onclick: (event) => {
					if (event.metaKey || event.ctrlKey) return
					event.preventDefault()
					go(path)
				},
			},
			...children
		)
	}

	function chip(text, tone) {
		return h('span', { class: 'chip' + (tone ? ' chip-' + tone : '') }, text)
	}

	function button(label, onclick, variant) {
		return h('button', { type: 'button', class: 'btn' + (variant ? ' btn-' + variant : ''), onclick }, label)
	}

	function toast(message, tone) {
		const el = h('div', { class: 'toast' + (tone ? ' toast-' + tone : ''), role: 'status' }, message)
		document.body.appendChild(el)
		setTimeout(() => el.remove(), 4000)
	}

	function dialog({ title, body, confirmLabel, input, inputLabel, required }) {
		return new Promise((resolve) => {
			const field = input
				? h('textarea', { class: 'input', rows: 4, 'aria-label': inputLabel || title, value: input === true ? '' : input })
				: null
			const close = (value) => {
				backdrop.remove()
				resolve(value)
			}
			const confirm = button(confirmLabel || __('Confirm'), () => {
				if (field && required && !field.value.trim()) {
					field.focus()
					return
				}
				close(field ? field.value.trim() : true)
			}, 'primary')
			const backdrop = h(
				'div',
				{ class: 'backdrop', onclick: (event) => event.target === backdrop && close(null) },
				h(
					'div',
					{ class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
					h('h2', null, title),
					body ? h('div', { class: 'dialog-body' }, body) : null,
					field,
					h('div', { class: 'row end' }, button(__('Cancel'), () => close(null), 'ghost'), confirm)
				)
			)
			document.body.appendChild(backdrop)
			;(field || confirm).focus()
		})
	}

	// ----------------------------------------------------------------- server

	function serverMessage(data) {
		try {
			const messages = JSON.parse(data._server_messages || '[]').map((item) => JSON.parse(item).message)
			if (messages.length) {
				const tmp = document.createElement('div')
				tmp.innerHTML = messages.join(' ')
				return tmp.textContent
			}
		} catch (error) {
			/* fall through */
		}
		return data.exception ? String(data.exception).split(':').slice(1).join(':').trim() : null
	}

	async function api(method, args, post) {
		const url = '/api/method/lms_copilot.api.' + method
		let response
		if (post) {
			response = await fetch(url, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Frappe-CSRF-Token': CSRF },
				body: JSON.stringify(args || {}),
			})
		} else {
			const query = new URLSearchParams()
			for (const [key, value] of Object.entries(args || {})) {
				if (value !== null && value !== undefined) query.set(key, typeof value === 'object' ? JSON.stringify(value) : value)
			}
			response = await fetch(url + '?' + query, { headers: { Accept: 'application/json' } })
		}
		const data = await response.json().catch(() => ({}))
		if (!response.ok) throw new Error(serverMessage(data) || __('Something went wrong. Please try again.'))
		return data.message
	}

	async function run(action, success) {
		try {
			const result = await action()
			if (success) toast(success, 'ok')
			return result
		} catch (error) {
			toast(error.message, 'error')
			return undefined
		}
	}

	// ----------------------------------------------------------------- format

	function parseDate(value) {
		return value ? new Date(String(value).replace(' ', 'T')) : null
	}

	function formatDate(value) {
		const date = parseDate(value)
		return date ? date.toLocaleString(document.documentElement.lang || undefined, { dateStyle: 'short', timeStyle: 'short' }) : ''
	}

	function ago(value) {
		const date = parseDate(value)
		if (!date) return ''
		const minutes = Math.max(0, Math.round((Date.now() + state.clockOffset - date.getTime()) / 60000))
		if (minutes < 60) return __('{0} min', [minutes])
		const hours = Math.round(minutes / 60)
		if (hours < 24) return __('{0} h', [hours])
		return __('{0} d', [Math.round(hours / 24)])
	}

	const CONFIDENCE = {
		High: [() => __('High'), 'ok'],
		Medium: [() => __('Medium'), 'warn'],
		Low: [() => __('Low'), 'danger'],
	}

	function confidenceChip(value) {
		const entry = CONFIDENCE[value]
		return entry ? chip(entry[0](), entry[1]) : chip('—')
	}

	const KINDS = {
		feedback: () => __('Project feedback'),
		'Lesson Change': () => __('Lesson change'),
		'Lesson Quiz': () => __('Lesson quiz'),
		'Learner Reminder': () => __('Learner reminder'),
		Escalation: () => __('Question for you'),
		Rubric: () => __('Rubric'),
	}

	const STATUS_TONE = {
		Pending: 'warn',
		'Pending Review': 'warn',
		Approved: 'ok',
		Applied: 'ok',
		'Feedback Sent': 'ok',
		Rejected: 'muted',
		Expired: 'muted',
		Superseded: 'muted',
		Failed: 'danger',
		Error: 'danger',
		Pass: 'ok',
		Fail: 'warn',
	}

	function statusChip(status) {
		return chip(__(status), STATUS_TONE[status])
	}

	// ----------------------------------------------------------------- layout

	function shell(crumbs, ...content) {
		const nav = [h('a', { href: '/lms', class: 'nav-link' }, __('Back to LMS'))]
		if (state.session && state.session.is_teacher) nav.unshift(link('', __('Review queue')))
		return [
			h(
				'header',
				{ class: 'topbar' },
				h('span', { class: 'brand' }, __('Learning Copilot')),
				h('nav', { class: 'nav' }, nav),
				h('span', { class: 'spacer' }),
				h('span', { class: 'muted small user' }, state.session ? state.session.full_name : '')
			),
			h('main', { class: 'page' }, crumbs ? h('div', { class: 'crumbs' }, crumbs) : null, ...content),
		]
	}

	function paint(...nodes) {
		clear(root)
		append(root, nodes)
		window.scrollTo(0, 0)
	}

	function message(title, text) {
		paint(shell(null, h('section', { class: 'empty' }, h('h1', null, title), text ? h('p', { class: 'muted' }, text) : null)))
	}

	// ------------------------------------------------------------------ queue

	async function renderQueue(kind) {
		const data = await api('get_review_queue')
		const rows = kind ? data.rows.filter((row) => row.kind === kind) : data.rows
		const filters = [
			filterChip(__('All'), data.total, !kind, ''),
			...Object.keys(KINDS).map((key) => filterChip(KINDS[key](), data.counts[key] || 0, kind === key, key)),
		]
		const quick = data.quick_approve.length
			? button(__('Quick-approve high-confidence feedback ({0})', [data.quick_approve.length]), () => quickApprove(data), 'ghost')
			: null

		const table = rows.length
			? h(
					'table',
					{ class: 'table queue' },
					h(
						'thead',
						null,
						h('tr', null, [__('Type'), __('Content'), __('Learner'), __('AI confidence'), __('Waiting'), ''].map((label) => h('th', null, label)))
					),
					h('tbody', null, rows.map(queueRow))
			  )
			: h('p', { class: 'empty muted' }, __('Nothing is waiting for you. Everything the assistant drafts will appear here.'))

		paint(
			shell(
				null,
				h('h1', null, __('Review queue')),
				h('p', { class: 'lead muted' }, __('Everything the assistant drafts waits here until you approve it.')),
				h('div', { class: 'row wrap toolbar' }, filters, h('span', { class: 'spacer' }), quick),
				h('div', { class: 'card flush' }, table)
			)
		)
	}

	function filterChip(label, count, active, key) {
		return button(label + ' ' + count, () => go(key ? '?kind=' + encodeURIComponent(key) : ''), active ? 'chip-active' : 'chip-button')
	}

	function queueRow(row) {
		const detail = []
		if (row.kind === 'feedback') {
			if (row.detail.tests) detail.push(__('Tests {0} passed', [row.detail.tests]))
			if (row.detail.flagged) detail.push(__('{0} criteria need a closer look', [row.detail.flagged]))
		} else if (row.detail.summary) {
			detail.push(row.detail.summary)
		}
		const path = (row.kind === 'feedback' ? 'review/' : 'proposal/') + encodeURIComponent(row.name)
		return h(
			'tr',
			null,
			h('td', null, chip(KINDS[row.kind] ? KINDS[row.kind]() : row.kind, row.kind === 'Escalation' ? 'warn' : 'ai')),
			h(
				'td',
				null,
				h('div', null, row.title || row.name),
				row.course_title ? h('div', { class: 'muted small' }, row.course_title) : null,
				detail.length ? h('div', { class: 'muted small' }, detail.join(' · ')) : null
			),
			h('td', null, row.who || '—'),
			h('td', null, row.kind === 'Escalation' ? '—' : confidenceChip(row.confidence)),
			h('td', { class: 'nowrap' }, ago(row.created)),
			h('td', { class: 'right' }, link(path, h('span', { class: 'btn btn-small' + (row.confidence === 'High' ? '' : ' btn-primary') }, __('Open'))))
		)
	}

	async function quickApprove(data) {
		const rows = data.rows.filter((row) => data.quick_approve.includes(row.name))
		const ok = await dialog({
			title: __('Approve {0} feedback drafts?', [rows.length]),
			body: [
				h('p', null, __('Every criterion in these drafts has high confidence. They will be sent to learners unchanged.')),
				h('ul', { class: 'list' }, rows.map((row) => h('li', null, (row.who || '') + ' — ' + (row.title || row.name)))),
			],
			confirmLabel: __('Approve and send'),
		})
		if (!ok) return
		const result = await run(() => api('bulk_approve_feedback', { drafts: data.quick_approve }, true))
		if (result) {
			toast(__('{0} sent, {1} skipped', [result.approved.length, result.skipped.length]), 'ok')
			render()
		}
	}

	// ----------------------------------------------------------------- review

	function repoParts(url) {
		const match = /^https:\/\/github\.com\/([^/]+)\/([^/]+)$/.exec(url || '')
		return match ? { owner: match[1], repo: match[2] } : null
	}

	function githubLine(submission, citation) {
		const ref = submission.commit || 'HEAD'
		let url = submission.repo_url + '/blob/' + ref + '/' + citation.file
		if (citation.line_start) url += '#L' + citation.line_start + (citation.line_end > citation.line_start ? '-L' + citation.line_end : '')
		return url
	}

	function citationLabel(citation) {
		if (citation.label) return citation.label
		if (citation.file) {
			const name = citation.file.split('/').pop()
			return name + ':' + citation.line_start + (citation.line_end > citation.line_start ? '–' + citation.line_end : '')
		}
		return citation.lesson
	}

	async function renderReview(name) {
		const data = await api('get_feedback_review', { draft: name })
		const open = data.status === 'Pending Review'
		const levels = {}
		data.scores.forEach((score) => (levels[score.criterion] = score.final_level || score.level))
		let resultTouched = Boolean(data.result_status)
		const resultSelect = h(
			'select',
			{ class: 'input', 'aria-label': __('Result'), disabled: !open, onchange: () => (resultTouched = true) },
			['Pass', 'Fail', 'Not Graded'].map((value) => h('option', { value }, __(value)))
		)
		const syncResult = () => {
			if (!resultTouched) resultSelect.value = Object.values(levels).every((level) => level > 1) ? 'Pass' : 'Fail'
		}
		if (data.result_status) resultSelect.value = data.result_status
		syncResult()

		const messageBox = h('textarea', { class: 'input message', rows: 8, disabled: !open, 'aria-label': __('Message to the learner') })
		messageBox.value = data.final_message || data.message || ''

		const evidence = evidencePane(data)
		const cards = data.scores.map((score) => scoreCard(score, levels, open, evidence, syncResult))

		const edits = () => ({ scores: levels, message: messageBox.value })
		const nextPath = () => (data.neighbours.next ? 'review/' + encodeURIComponent(data.neighbours.next) : '')
		const actions = open
			? [
					button(__('Approve and send'), async () => {
						const result = await run(() => api('approve_feedback', { draft: name, ...edits(), result_status: resultSelect.value }, true))
						if (result) {
							toast(__('Feedback sent to the learner.'), 'ok')
							go(nextPath())
						}
					}, 'primary'),
					button(__('Save draft'), () => run(() => api('save_feedback_draft', { draft: name, ...edits() }, true), __('Draft saved.'))),
					button(__('Ask the assistant to rewrite…'), async () => {
						const note = await dialog({
							title: __('What should the assistant change?'),
							input: true,
							inputLabel: __('Instructions'),
							required: true,
							confirmLabel: __('Send to assistant'),
						})
						if (note && (await run(() => api('request_feedback_rewrite', { draft: name, note }, true), __('Sent back to the assistant.')))) go(nextPath())
					}, 'ghost'),
					h('span', { class: 'spacer' }),
					button(__('Grade it myself'), async () => {
						const note = await dialog({
							title: __('Discard this draft?'),
							body: h('p', null, __('The learner sees nothing from it. Grade the submission in the LMS as usual.')),
							input: true,
							inputLabel: __('Reason (optional)'),
							confirmLabel: __('Discard draft'),
						})
						if (note !== null && (await run(() => api('reject_feedback', { draft: name, note }, true), __('Draft discarded.')))) go(nextPath())
					}, 'ghost'),
			  ]
			: [statusChip(data.status), data.edit_level ? chip(__('Edit: {0}', [__(data.edit_level)])) : null, data.reviewed_by ? h('span', { class: 'muted small' }, __('by {0}', [data.reviewed_by])) : null]

		const nav = h(
			'div',
			{ class: 'row' },
			data.neighbours.previous ? link('review/' + encodeURIComponent(data.neighbours.previous), h('span', { class: 'btn btn-ghost btn-small' }, __('← Previous'))) : null,
			data.neighbours.next ? link('review/' + encodeURIComponent(data.neighbours.next), h('span', { class: 'btn btn-ghost btn-small' }, __('Next →'))) : null
		)

		paint(
			shell(
				[link('', __('Review queue')), ' / ', h('b', null, (data.assignment_title || data.assignment) + ' — ' + (data.learner_name || ''))],
				h(
					'div',
					{ class: 'row wrap toolbar' },
					data.submission.commit ? chip(__('commit {0}', [data.submission.commit.slice(0, 7)])) : null,
					chip(__('Submitted {0}', [formatDate(data.submission.submitted_on)])),
					data.course_title ? chip(data.course_title) : null,
					h('span', { class: 'spacer' }),
					nav
				),
				h(
					'div',
					{ class: 'split' },
					h('section', { class: 'card pane' }, evidence.node),
					h(
						'section',
						{ class: 'card pane' },
						h(
							'div',
							{ class: 'row' },
							h('h2', null, __('Draft feedback')),
							chip(__('Drafted by AI'), 'ai'),
							h('span', { class: 'spacer' }),
							data.rubric ? chip(__('Rubric: {0}', [data.rubric.title])) : null
						),
						h('div', { class: 'stack' }, cards),
						h('label', { class: 'field' }, h('span', { class: 'muted small' }, __('Message to the learner (editable)')), messageBox),
						h('label', { class: 'field inline' }, h('span', { class: 'muted small' }, __('Result')), resultSelect),
						h('div', { class: 'row wrap footer' }, actions)
					)
				)
			)
		)
		evidence.start()
	}

	function scoreCard(score, levels, open, evidence, onChange) {
		const flagged = score.confidence !== 'High'
		const buttons = h('div', { class: 'levels', role: 'group', 'aria-label': __('Level for {0}', [score.criterion]) })
		const drawLevels = () => {
			clear(buttons)
			for (let level = 1; level <= (score.max_level || 3); level++) {
				buttons.appendChild(
					h(
						'button',
						{
							type: 'button',
							class: 'level' + (levels[score.criterion] === level ? ' on' : '') + (score.level === level ? ' ai' : ''),
							'aria-pressed': levels[score.criterion] === level ? 'true' : 'false',
							title: score.level === level ? __('Level suggested by the assistant') : null,
							disabled: !open,
							onclick: () => {
								levels[score.criterion] = level
								drawLevels()
								onChange()
							},
						},
						String(level)
					)
				)
			}
		}
		drawLevels()
		return h(
			'div',
			{ class: 'card score' + (flagged ? ' flagged' : '') },
			h('div', { class: 'row' }, h('b', null, score.criterion), h('span', { class: 'spacer' }), buttons),
			h('p', { class: 'small' }, score.reason),
			score.citations.length
				? h(
						'div',
						{ class: 'row wrap' },
						score.citations.map((citation) =>
							citation.file
								? h('button', { type: 'button', class: 'cite', onclick: () => evidence.show(citation) }, citationLabel(citation))
								: h('span', { class: 'cite' }, citationLabel(citation))
						)
				  )
				: null,
			flagged ? h('div', { class: 'conf' }, __('Confidence: {0} · needs a closer look', [CONFIDENCE[score.confidence] ? CONFIDENCE[score.confidence][0]() : score.confidence])) : null
		)
	}

	function evidencePane(data) {
		const submission = data.submission
		const files = []
		data.scores.forEach((score) =>
			score.citations.forEach((citation) => {
				if (citation.file && !files.includes(citation.file)) files.push(citation.file)
			})
		)
		const body = h('div', { class: 'tab-body' })
		const tabs = h('div', { class: 'tabs', role: 'tablist' })
		const tests = submission.tests
		const tabDefs = [
			['code', __('Code')],
			['tests', __('Test results ({0}/{1})', [tests.passed, tests.total])],
			['history', __('Submission history')],
		]
		let current = null
		const select = (key, citation) => {
			current = key
			clear(tabs)
			tabDefs.forEach(([id, label]) =>
				tabs.appendChild(h('button', { type: 'button', role: 'tab', class: 'tab' + (id === key ? ' on' : ''), 'aria-selected': id === key ? 'true' : 'false', onclick: () => select(id) }, label))
			)
			clear(body)
			if (key === 'code') body.appendChild(codeView(citation))
			if (key === 'tests') body.appendChild(testList(tests))
			if (key === 'history') body.appendChild(historyList(submission.history))
		}
		const codeView = (citation) => {
			const file = citation ? citation.file : files[0]
			const wrap = h('div', null)
			wrap.appendChild(
				h(
					'div',
					{ class: 'row wrap small' },
					h('a', { href: submission.repo_url, target: '_blank', rel: 'noopener' }, submission.repo_url),
					h('span', { class: 'spacer' }),
					files.length > 1
						? h(
								'select',
								{ class: 'input compact', 'aria-label': __('File'), onchange: (event) => select('code', { file: event.target.value }) },
								files.map((path) => h('option', { value: path, selected: path === file }, path))
						  )
						: null
				)
			)
			if (!file) {
				wrap.appendChild(h('p', { class: 'muted' }, __('The draft cites no files. Open the repository to read the code.')))
				return wrap
			}
			const ranges = []
			data.scores.forEach((score) => score.citations.forEach((c) => c.file === file && ranges.push([c.line_start, c.line_end || c.line_start])))
			const view = h('div', { class: 'code' }, h('p', { class: 'muted small pad' }, __('Loading {0}…', [file])))
			wrap.appendChild(h('div', { class: 'row small' }, h('code', null, file), h('span', { class: 'spacer' }), h('a', { href: githubLine(submission, { file, line_start: citation && citation.line_start, line_end: citation && citation.line_end }), target: '_blank', rel: 'noopener' }, __('Open on GitHub'))))
			wrap.appendChild(view)
			loadFile(submission, file).then(
				(text) => {
					clear(view)
					text.split('\n').forEach((line, index) => {
						const number = index + 1
						const hit = ranges.some(([start, end]) => number >= start && number <= end)
						view.appendChild(h('div', { class: 'line' + (hit ? ' hl' : ''), id: 'L' + number }, h('span', { class: 'ln' }, String(number)), h('span', null, line)))
					})
					if (citation && citation.line_start) {
						const target = view.querySelector('#L' + citation.line_start)
						if (target) target.scrollIntoView({ block: 'center' })
					}
				},
				() => {
					clear(view)
					view.appendChild(h('p', { class: 'muted small pad' }, __('Could not load this file from GitHub. The repository may be private or the file moved.')))
				}
			)
			return wrap
		}
		return {
			node: h('div', null, tabs, body),
			start: () => select(files.length ? 'code' : 'tests'),
			show: (citation) => select('code', citation),
			get current() {
				return current
			},
		}
	}

	const fileCache = {}
	function loadFile(submission, file) {
		const parts = repoParts(submission.repo_url)
		if (!parts) return Promise.reject(new Error('repo'))
		const url = ['https://raw.githubusercontent.com', parts.owner, parts.repo, submission.commit || 'HEAD', file].map((part, index) => (index < 1 ? part : part.split('/').map(encodeURIComponent).join('/'))).join('/')
		if (!fileCache[url]) {
			fileCache[url] = fetch(url, { credentials: 'omit' }).then((response) => (response.ok ? response.text() : Promise.reject(new Error(String(response.status)))))
		}
		return fileCache[url]
	}

	function testList(tests) {
		if (!tests.total) return h('p', { class: 'muted' }, __('No test results yet.'))
		return h(
			'ul',
			{ class: 'tests' },
			tests.results.map((test) =>
				h('li', { class: test.passed ? 'pass' : 'fail' }, h('span', { class: 'mark', 'aria-hidden': 'true' }, test.passed ? '✓' : '✗'), h('span', { class: 'sr-only' }, test.passed ? __('Test passed') : __('Test did not pass')), h('span', null, test.name), test.message ? h('div', { class: 'muted small' }, test.message) : null)
			)
		)
	}

	function historyList(history) {
		return h(
			'table',
			{ class: 'table' },
			h('tbody', null, (history || []).map((row) => h('tr', null, h('td', null, formatDate(row.creation)), h('td', null, row.commit_sha ? row.commit_sha.slice(0, 7) : '—'), h('td', null, (row.tests_passed || 0) + '/' + (row.tests_total || 0)), h('td', null, statusChip(row.status)))))
		)
	}

	// --------------------------------------------------------------- proposal

	async function renderProposal(name) {
		const data = await api('get_proposal', { name })
		const open = data.status === 'Pending'
		const preview = data.preview || {}
		const params = data.final_params || data.params || {}
		let editor = null
		let replyBox = null
		const body = []

		if (data.summary) body.push(h('p', null, data.summary))
		if (preview.kind === 'diff') {
			body.push(diffView(preview.lines || []))
			if (data.type === 'Lesson Change' && open) {
				editor = h('textarea', { class: 'input mono', rows: 10, hidden: true, 'aria-label': __('Lesson text') })
				editor.value = params.markdown || ''
				body.push(editor)
			}
		} else if (preview.kind === 'message') {
			body.push(h('p', { class: 'muted small' }, __('To {0} learner(s): {1}', [preview.recipients.length, preview.recipients.join(', ')])))
			editor = h('textarea', { class: 'input', rows: 5, disabled: !open, 'aria-label': __('Reminder') })
			editor.value = params.message || preview.message || ''
			body.push(editor)
		} else if (preview.kind === 'question') {
			body.push(h('blockquote', { class: 'quote' }, preview.question))
			if (open) {
				replyBox = h('textarea', { class: 'input', rows: 5, 'aria-label': __('Your reply') })
				body.push(h('label', { class: 'field' }, h('span', { class: 'muted small' }, __('Your reply is sent to the learner as a notification.')), replyBox))
			} else if (params.reply) {
				body.push(h('div', { class: 'card' }, params.reply))
			}
		}

		const approveParams = () => {
			if (replyBox) return { reply: replyBox.value }
			if (editor && data.type === 'Lesson Change' && !editor.hidden) return { ...params, markdown: editor.value }
			if (editor && data.type === 'Learner Reminder') return { ...params, message: editor.value }
			return null
		}

		const actions = open
			? [
					button(data.type === 'Escalation' ? __('Send reply') : __('Approve and apply'), async () => {
						if (replyBox && !replyBox.value.trim()) {
							replyBox.focus()
							return
						}
						const result = await run(() => api('approve_proposal', { name, params: approveParams() }, true))
						if (!result) return
						if (result.status === 'Applied') toast(__('Applied and verified.'), 'ok')
						else toast(result.error || __(result.status), 'error')
						render()
					}, 'primary'),
					data.type === 'Lesson Change'
						? button(__('Edit'), () => {
								editor.hidden = !editor.hidden
								if (!editor.hidden) editor.focus()
						  })
						: null,
					button(__('Reject'), async () => {
						const note = await dialog({ title: __('Reject this proposal?'), input: true, inputLabel: __('Reason (optional)'), confirmLabel: __('Reject') })
						if (note !== null && (await run(() => api('reject_proposal', { name, note }, true), __('Rejected.')))) render()
					}, 'ghost'),
			  ]
			: [statusChip(data.status), data.reviewed_by ? h('span', { class: 'muted small' }, __('by {0}', [data.reviewed_by])) : null]

		paint(
			shell(
				[link('', __('Review queue')), ' / ', h('b', null, data.title || data.name)],
				h(
					'section',
					{ class: 'card proposal' },
					h(
						'div',
						{ class: 'row wrap' },
						h('h1', null, data.title || data.name),
						chip(KINDS[data.type] ? KINDS[data.type]() : data.type, 'ai'),
						statusChip(data.status)
					),
					h(
						'p',
						{ class: 'muted small' },
						[
							data.course_title,
							__('Requested by {0} ({1})', [data.requested_by_name || data.requested_by, __(data.requested_via || '')]),
							formatDate(data.created),
							open && data.expires_on ? __('Expires {0}', [formatDate(data.expires_on)]) : null,
						]
							.filter(Boolean)
							.join(' · ')
					),
					data.error ? h('p', { class: 'alert' }, data.error) : null,
					body,
					data.result && data.status === 'Applied' ? h('p', { class: 'muted small' }, __('Re-read after applying: the change is in place.')) : null,
					h('div', { class: 'row wrap footer' }, actions)
				)
			)
		)
	}

	function diffView(lines) {
		return h(
			'div',
			{ class: 'diff' },
			lines.map((line) => {
				const text = line.text.replace(/^[+-]\s?/, '')
				const sign = { add: '+ ', del: '- ', skip: '  ', same: '  ' }[line.op]
				return h('div', { class: 'diff-' + line.op }, sign + text)
			})
		)
	}

	// ---------------------------------------------------------------- insight

	async function renderInsight(course) {
		const data = await api('get_weekly_insight', { course })
		if (!data) {
			message(__('No weekly report yet'), __('The assistant has not written a report for this course.'))
			return
		}
		const max = Math.max(1, ...data.groups.map((group) => group.count || group.learners || 0))
		paint(
			shell(
				[link('', __('Review queue')), ' / ', h('b', null, data.course_title), ' · ', __('Week of {0}', [data.week_start])],
				h('div', { class: 'row wrap toolbar' }, Object.entries(data.stats || {}).map(([key, value]) => chip(__(key) + ': ' + value))),
				h(
					'div',
					{ class: 'split' },
					h(
						'section',
						{ class: 'card pane' },
						h('h2', null, __('Where the class is stuck')),
						h(
							'div',
							{ class: 'bars' },
							data.groups.map((group) => {
								const value = group.count || group.learners || 0
								return h('div', { class: 'bar' }, h('span', null, group.title), h('span', { class: 'track' }, h('i', { style: 'width:' + Math.round((value / max) * 100) + '%' })), h('span', null, String(value)))
							})
						),
						data.at_risk.length ? h('h3', null, __('Learners needing attention')) : null,
						data.at_risk.length ? h('table', { class: 'table' }, h('tbody', null, data.at_risk.map((item) => h('tr', null, h('td', null, item.learner || ''), h('td', null, item.reason || ''))))) : null
					),
					h(
						'section',
						{ class: 'pane stack' },
						data.groups.map((group) =>
							h(
								'div',
								{ class: 'card' },
								h('div', { class: 'row' }, h('b', null, group.title), h('span', { class: 'spacer' }), group.learners ? chip(__('{0} learners', [group.learners])) : null),
								h('p', { class: 'small' }, group.summary),
								group.evidence && group.evidence.length
									? h('details', { class: 'small' }, h('summary', null, __('Evidence ({0})', [group.evidence.length])), h('ul', { class: 'list' }, group.evidence.map((item) => h('li', null, typeof item === 'string' ? item : item.label || JSON.stringify(item)))))
									: null,
								group.suggestion ? h('div', { class: 'box small' }, h('b', null, __('Suggestion: ')), group.suggestion) : null,
								h('div', { class: 'row wrap' }, (group.proposals || []).map((proposal) => link('proposal/' + encodeURIComponent(proposal), h('span', { class: 'btn btn-small' }, __('Review proposal')), ' ', statusChip(group.proposal_status[proposal] || ''))))
							)
						)
					)
				)
			)
		)
	}

	// ----------------------------------------------------------------- submit

	const STEPS = {
		submitted: () => __('Submitted'),
		tested: () => __('Automatic tests ran'),
		review: () => __('Waiting for your teacher to review the feedback'),
		feedback: () => __('Feedback received'),
	}

	async function renderSubmit(assignment) {
		const data = await api('get_assignment', { assignment })
		const latest = data.submissions[0]
		const input = h('input', { class: 'input grow', type: 'url', placeholder: 'https://github.com/you/project', 'aria-label': __('Repository link'), value: latest ? latest.repo_url : '' })
		const submit = button(latest ? __('Submit again') : __('Submit'), async () => {
			submit.disabled = true
			const result = await run(() => api('submit_project', { assignment, repo_url: input.value.trim() }, true), __('Submitted. Tests will run shortly.'))
			submit.disabled = false
			if (result) render()
		}, 'primary')
		const brief = h('div', { class: 'prose' })
		brief.innerHTML = data.question || '' // sanitised by the server

		paint(
			shell(
				[data.course_title ? data.course_title + ' / ' : '', h('b', null, data.title)],
				h(
					'div',
					{ class: 'split wide-left' },
					h(
						'section',
						{ class: 'card pane stack' },
						h('h1', null, data.title),
						brief,
						data.rubric
							? [
									h('h3', null, __('How it is graded')),
									h('ul', { class: 'list' }, data.rubric.criteria.map((criterion) => h('li', null, h('b', null, criterion.criterion), criterion.description ? ' — ' + criterion.description : ''))),
							  ]
							: null,
						h('h3', null, __('Submit your project')),
						h('form', { class: 'row', onsubmit: (event) => (event.preventDefault(), submit.click()) }, input, submit),
						h('p', { class: 'muted small' }, __('The repository must be public on GitHub.'))
					),
					h('section', { class: 'card pane' }, latest ? submissionStatus(latest) : h('p', { class: 'muted' }, __('You have not submitted this project yet.')))
				)
			)
		)
	}

	function submissionStatus(submission) {
		return [
			h('h2', null, __('Status')),
			h(
				'ol',
				{ class: 'timeline' },
				submission.timeline.map((step) =>
					h('li', { class: step.done ? 'done' : step.current ? 'current' : '' }, h('span', { class: 'dot', 'aria-hidden': 'true' }), h('div', null, h('b', null, STEPS[step.step]()), step.at ? h('div', { class: 'muted small' }, formatDate(step.at)) : null))
				)
			),
			submission.status === 'Error' ? h('p', { class: 'alert' }, __('We could not process this submission. Your teacher has been told.')) : null,
			submission.tests ? [h('h3', null, __('Test results ({0}/{1})', [submission.tests.passed, submission.tests.total])), testList(submission.tests)] : null,
			submission.feedback
				? [
						h('h3', null, __('Feedback from your teacher')),
						submission.feedback.result ? statusChip(submission.feedback.result) : null,
						h('div', { class: 'card feedback' }, submission.feedback.message),
						h('ul', { class: 'list' }, submission.feedback.scores.map((score) => h('li', null, score.criterion + ': ' + score.final_level + '/' + score.max_level))),
				  ]
				: submission.tests
				? h('p', { class: 'box small' }, __('While you wait, look at the tests that did not pass.'))
				: null,
		]
	}

	// ----------------------------------------------------------------- router

	function go(path) {
		history.pushState(null, '', '/copilot/' + path)
		render()
	}

	async function render() {
		const parts = location.pathname.replace(/^\/copilot\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
		const kind = new URLSearchParams(location.search).get('kind')
		try {
			if (!state.session) {
				state.session = await api('get_session_context')
				// Server datetimes carry no zone: compare them with the server's clock, not ours.
				state.clockOffset = parseDate(state.session.server_now).getTime() - Date.now()
			}
			const [screen, id] = parts
			if (screen === 'submit' && id) return await renderSubmit(id)
			if (!state.session.is_teacher) return message(__('Learning Copilot'), __('Open this page from a project assignment to submit your work.'))
			if (screen === 'review' && id) return await renderReview(id)
			if (screen === 'proposal' && id) return await renderProposal(id)
			if (screen === 'insight' && id) return await renderInsight(id)
			return await renderQueue(kind)
		} catch (error) {
			message(__('This page could not be loaded'), error.message)
		}
	}

	window.addEventListener('popstate', render)
	render()
})()
