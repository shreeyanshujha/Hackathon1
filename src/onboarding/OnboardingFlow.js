// Multi-step onboarding controller. Owns the draft, per-step validation, and
// the final build → validate → persist handoff. Progress autosaves so a
// half-finished setup survives an app restart.

import React, { useEffect, useRef, useState } from 'react';
import { View, Text, ScrollView, KeyboardAvoidingView, Platform, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { colors, type } from '../theme';
import { Button, InfoBanner } from '../components/ui';
import { validateStep, buildProfile, validateProfile } from '../model/profile';
import { saveProfile, saveDraft, clearDraft } from '../storage/profileStore';

import WelcomeStep from './steps/WelcomeStep';
import DemographicsStep from './steps/DemographicsStep';
import ContactsStep from './steps/ContactsStep';
import SleepStep from './steps/SleepStep';
import RoutineStep from './steps/RoutineStep';
import HobbiesMobilityStep from './steps/HobbiesMobilityStep';
import LifestyleStep from './steps/LifestyleStep';
import HealthStep from './steps/HealthStep';
import ConsentStep from './steps/ConsentStep';
import ReviewStep from './steps/ReviewStep';

const STEPS = [
  { key: 'welcome', title: 'Welcome', subtitle: 'A few minutes now, real peace of mind later.', component: WelcomeStep },
  { key: 'demographics', title: 'About you', subtitle: 'The basics.', component: DemographicsStep },
  { key: 'contacts', title: 'Emergency contacts', subtitle: 'Who we reach out to if something looks wrong.', component: ContactsStep },
  { key: 'sleep', title: 'Sleep baseline', subtitle: 'What normal nights look like.', component: SleepStep },
  { key: 'routine', title: 'Weekly routine', subtitle: 'The regular rhythm of your week.', component: RoutineStep },
  { key: 'hobbiesMobility', title: 'Hobbies & mobility', subtitle: 'How you like to spend time, and how you get around.', component: HobbiesMobilityStep },
  { key: 'lifestyle', title: 'Lifestyle', subtitle: 'Diet, smoking and alcohol.', component: LifestyleStep },
  { key: 'health', title: 'Health context', subtitle: 'Optional background for emergencies.', component: HealthStep },
  { key: 'consent', title: 'Consent & privacy', subtitle: 'You decide what is monitored and who sees it.', component: ConsentStep },
  { key: 'review', title: 'Review & finish', subtitle: 'One last look before we save your baseline.', component: ReviewStep },
];

export default function OnboardingFlow({ initialDraft, onComplete }) {
  const [stepIndex, setStepIndex] = useState(0);
  const [draft, setDraftState] = useState(initialDraft);
  const [errors, setErrors] = useState({});
  const [finishProblems, setFinishProblems] = useState([]);
  const scrollRef = useRef(null);

  const setDraft = (patch) => setDraftState((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    saveDraft(draft).catch(() => {});
  }, [draft]);

  const step = STEPS[stepIndex];
  const StepComponent = step.component;

  const scrollToTop = () => scrollRef.current?.scrollTo({ y: 0, animated: false });

  const finish = async () => {
    const profile = buildProfile(draft);
    const problems = validateProfile(profile);
    if (problems.length > 0) {
      setFinishProblems(problems);
      scrollToTop();
      return;
    }
    await saveProfile(profile);
    await clearDraft();
    onComplete(profile);
  };

  const goNext = () => {
    const stepErrors = validateStep(step.key, draft);
    setErrors(stepErrors);
    if (Object.keys(stepErrors).length > 0) return;
    if (step.key === 'review') {
      finish();
      return;
    }
    setStepIndex((i) => Math.min(i + 1, STEPS.length - 1));
    scrollToTop();
  };

  const goBack = () => {
    if (stepIndex === 0) return;
    setErrors({});
    setStepIndex((i) => i - 1);
    scrollToTop();
  };

  const jumpTo = (key) => {
    const index = STEPS.findIndex((s) => s.key === key);
    if (index >= 0) {
      setErrors({});
      setStepIndex(index);
      scrollToTop();
    }
  };

  const nextTitle = step.key === 'review' ? 'Finish & save' : step.key === 'welcome' ? 'Get started' : 'Next';

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <View style={styles.header}>
        <View style={styles.progressTrack}>
          {STEPS.map((s, i) => (
            <View key={s.key} style={[styles.progressSegment, i <= stepIndex && styles.progressSegmentDone]} />
          ))}
        </View>
        <Text style={styles.stepCount}>
          Step {stepIndex + 1} of {STEPS.length}
        </Text>
        <Text style={type.title}>{step.title}</Text>
        <Text style={[type.subtitle, { marginTop: 4 }]}>{step.subtitle}</Text>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          ref={scrollRef}
          style={{ flex: 1 }}
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
        >
          {Object.keys(errors).length > 0 && (
            <InfoBanner tone="danger">Please fix the highlighted fields below.</InfoBanner>
          )}
          <StepComponent
            draft={draft}
            setDraft={setDraft}
            errors={errors}
            jumpTo={jumpTo}
            finishProblems={finishProblems}
          />
        </ScrollView>

        <View style={styles.footer}>
          {stepIndex > 0 && <Button title="Back" variant="secondary" onPress={goBack} style={styles.backButton} />}
          <Button title={nextTitle} onPress={goNext} style={{ flex: 1 }} />
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  header: { paddingHorizontal: 20, paddingTop: 10, paddingBottom: 14 },
  progressTrack: { flexDirection: 'row', gap: 4, marginBottom: 12 },
  progressSegment: { flex: 1, height: 5, borderRadius: 3, backgroundColor: colors.border },
  progressSegmentDone: { backgroundColor: colors.primary },
  stepCount: { ...type.small, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 },
  scrollContent: { paddingHorizontal: 20, paddingBottom: 24 },
  footer: {
    flexDirection: 'row',
    gap: 12,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 8,
    backgroundColor: colors.bg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  backButton: { paddingHorizontal: 26 },
});
