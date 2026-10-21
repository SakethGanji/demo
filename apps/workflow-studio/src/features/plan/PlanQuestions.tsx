/**
 * The requirements checklist: one card per item, grouped under headings (People and access, Data, Integrations, …).
 * An item with options shows them as chips (checkboxes when several can be picked, radios when one), the suggested
 * ones preselected and marked, and a free-text "other" next to them; an item without options is a free-text answer.
 * An answer stays "suggested" until the user changes or keeps it. The side panel shows "n of m answered", sends the
 * round, and "Accept all suggestions" takes every suggested pick and moves on to build in one click.
 */
import { useState } from 'react'
import { motion } from 'motion/react'
import { Check, CornerDownRight, MessageCircleQuestion, Sparkles, WandSparkles } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Spinner } from '@/shared/components/ui/spinner'
import { Textarea } from '@/shared/components/ui/textarea'
import { Progress, SectionHeader, Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { PlanColumns } from './PlanSteps'
import { FADE, SPRING, useCalm } from './motion'
import { confirmSuggested, groupByHeading, initialAnswers, isChoice, parseAnswer, questionProgress, setOther, toggleChoice, unanswered } from './planLogic'
import type { PlanQuestion } from './planTypes'

export function PlanQuestions({
  questions,
  round,
  pending,
  onSubmit,
  onAcceptAll,
}: {
  questions: PlanQuestion[]
  round: number
  pending: boolean
  onSubmit: (answers: Record<string, string>) => void
  /** "Accept all suggestions": the server takes every suggested pick; the caller moves on to build */
  onAcceptAll?: () => void
}) {
  const [answers, setAnswers] = useState<Record<string, string>>(() => initialAnswers(questions))
  const [confirmed, setConfirmed] = useState<Set<string>>(() => new Set())
  const open = unanswered(questions, answers)
  const progress = questionProgress(questions, answers, confirmed)
  const groups = groupByHeading(questions)
  const index = new Map(questions.map((q, n) => [q.id, n + 1]))

  const confirm = (id: string) => setConfirmed((c) => new Set(c).add(id))
  const set = (id: string, v: string) => {
    setAnswers((a) => ({ ...a, [id]: v }))
    confirm(id)
  }

  return (
    <PlanColumns
      main={
        <>
          <Tile className="p-4" data-testid="plan-questions">
            <SectionHeader
              title={round > 1 ? 'One more thing' : 'What a real deployment needs'}
              sub={`${questions.length} question${questions.length === 1 ? '' : 's'}`}
              icon={<MessageCircleQuestion />}
              hue="ai"
            />
            <p className="mt-2 text-[12.5px] leading-[19px] text-muted-foreground">
              Every item has a suggested choice already picked. Change what's wrong, add your own under "Other", or accept
              them all. You review the whole plan before anything is built.
            </p>
          </Tile>
          {groups.map((g) => (
            <section key={g.heading} className="flex flex-col gap-2.5" data-testid={`plan-topic-${g.questions[0]?.topic}`}>
              <h2 className="px-1 text-[12.5px] font-semibold text-muted-foreground">
                {g.heading} <span className="font-normal">· {g.questions.length}</span>
              </h2>
              {g.questions.map((q) => (
                <QuestionCard
                  key={q.id}
                  q={q}
                  n={index.get(q.id) ?? 0}
                  answer={answers[q.id] ?? ''}
                  confirmed={confirmed.has(q.id)}
                  onAnswer={(v) => set(q.id, v)}
                  onKeep={() => confirm(q.id)}
                />
              ))}
            </section>
          ))}
        </>
      }
      aside={
        <Tile className="flex flex-col gap-3 p-4" data-testid="plan-questions-panel">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 @min-[960px]/plan:flex-col">
            <span className="text-[13.5px] font-semibold" data-testid="plan-question-progress">
              {progress.answered} of {progress.total} answered
            </span>
            <span className="text-[12px] text-muted-foreground">
              {progress.suggested ? `${progress.suggested} suggested` : null}
              {progress.suggested && progress.empty ? ' · ' : null}
              {progress.empty ? `${progress.empty} need${progress.empty === 1 ? 's' : ''} an answer` : null}
            </span>
          </div>
          <Progress value={progress.total ? (progress.answered / progress.total) * 100 : 0} />
          <div className="flex flex-wrap gap-2 @min-[960px]/plan:flex-col">
            <Button
              size="sm"
              className="flex-1 @min-[960px]/plan:flex-none"
              disabled={pending || !questions.length}
              onClick={() => (onAcceptAll ? onAcceptAll() : onSubmit(initialAnswers(questions)))}
              data-testid="plan-accept-all"
            >
              {pending ? <Spinner /> : <WandSparkles />} {pending ? 'Applying…' : 'Accept all suggestions'}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="flex-1 @min-[960px]/plan:flex-none"
              disabled={!progress.suggested}
              onClick={() => setConfirmed(confirmSuggested(questions, answers, confirmed))}
              data-testid="plan-use-suggested"
            >
              <WandSparkles /> Confirm the suggested rest
            </Button>
            <Button size="sm" variant="outline" className="flex-1 @min-[960px]/plan:flex-none" disabled={pending || open.length > 0} onClick={() => onSubmit(answers)} data-testid="plan-answers-submit">
              {pending ? <Spinner /> : <CornerDownRight />} {pending ? 'Updating the plan…' : 'Send answers'}
            </Button>
          </div>
          <p className="hidden text-[12px] leading-[18px] text-muted-foreground @min-[960px]/plan:block">
            {open.length
              ? `Answer ${open.length === 1 ? 'the last open question' : `the ${open.length} open questions`} to continue.`
              : 'The plan is updated with your answers. At most one more short round follows.'}
          </p>
        </Tile>
      }
    />
  )
}

export function QuestionCard({
  q,
  n,
  answer,
  confirmed,
  onAnswer,
  onKeep,
}: {
  q: PlanQuestion
  n: number
  answer: string
  confirmed: boolean
  onAnswer: (v: string) => void
  onKeep: () => void
}) {
  const calm = useCalm()
  const empty = !answer.trim()
  const suggested = !empty && !confirmed
  const choice = isChoice(q)
  const { picked, other } = parseAnswer(q, answer)
  const isSuggested = (o: string) => !!q.suggested?.includes(o)
  return (
    <motion.div
      initial={calm ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={calm ? { duration: 0 } : FADE}
      layout={calm ? false : 'position'}
    >
      <Tile className="flex flex-col gap-2 p-3.5" data-testid={`plan-question-${q.id}`} data-answer-state={empty ? 'empty' : suggested ? 'suggested' : 'answered'}>
        <div className="flex items-start gap-2.5">
          <span className="mt-px inline-flex size-5 shrink-0 items-center justify-center rounded-full glass-orb-clear text-[10.5px] font-semibold text-muted-foreground tabular-nums">
            {n}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] leading-5 font-medium whitespace-pre-line">{q.question}</div>
            {q.why ? <div className="mt-0.5 text-[12px] leading-[18px] text-muted-foreground">{q.why}</div> : null}
          </div>
          {empty ? (
            <Badge variant="warning" className="shrink-0">needs an answer</Badge>
          ) : suggested ? (
            <Badge variant="ai" className="shrink-0"><Sparkles /> suggested</Badge>
          ) : (
            <Badge variant="success" className="shrink-0"><Check /> answered</Badge>
          )}
        </div>
        {choice ? (
          <div className="flex flex-col gap-1.5 pl-7.5">
            <div className="flex flex-wrap gap-1.5" role={q.multi ? 'group' : 'radiogroup'} aria-label={q.question} data-testid={`plan-choices-${q.id}`}>
              {q.options!.map((o) => {
                const on = picked.includes(o)
                return (
                  <motion.span key={o} whileTap={calm ? undefined : { scale: 0.96 }} transition={SPRING} className="inline-flex">
                    <Button
                      size="xs"
                      variant={on ? 'secondary' : 'outline'}
                      type="button"
                      role={q.multi ? 'checkbox' : 'radio'}
                      aria-checked={on}
                      data-suggested={isSuggested(o) || undefined}
                      onClick={() => onAnswer(toggleChoice(q, answer, o))}
                    >
                      {on ? <Check /> : null} {o}
                      {isSuggested(o) ? <Sparkles className="text-ai" aria-label="suggested" /> : null}
                    </Button>
                  </motion.span>
                )
              })}
            </div>
            {q.other === false ? null : (
              <Input
                className={cn('max-w-md', empty && 'shadow-[0_0_0_2px_var(--warning-soft)]')}
                value={other}
                placeholder="Other (type your own)"
                onChange={(e) => onAnswer(setOther(q, answer, e.target.value))}
                aria-label={`Other for ${q.question.split('\n')[0]}`}
                data-testid={`plan-other-${q.id}`}
              />
            )}
            {suggested ? (
              <div className="flex items-center gap-2">
                <Button size="xs" variant="ghost" onClick={onKeep} data-testid={`plan-keep-${q.id}`}>
                  <Check /> Keep the suggestion
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5 pl-7.5">
            <Textarea
              className={cn('min-h-9', empty && 'shadow-[0_0_0_2px_var(--warning-soft)]', suggested && 'shadow-[0_0_0_2px_var(--ai-soft)]')}
              rows={2}
              value={answer}
              placeholder="Your answer"
              onChange={(e) => onAnswer(e.target.value)}
              aria-label={`Answer ${n}`}
              data-testid={`plan-answer-${q.id}`}
            />
            {suggested ? (
              <div className="flex items-center gap-2">
                <Button size="xs" variant="ghost" onClick={onKeep} data-testid={`plan-keep-${q.id}`}>
                  <Check /> Keep this answer
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </Tile>
    </motion.div>
  )
}
