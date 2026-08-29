import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { Link } from "react-router-dom";
import { get } from "../api";

gsap.registerPlugin(ScrollTrigger);

/* Scroll-scrubbed cinematic hero.
 *
 * The section pins and the page scroll drives `video.currentTime` rather than letting the
 * video play. Same technique Mercury uses on its own homepage (hero-scrub-lg.mp4).
 *
 * WHAT IS ON SCREEN IS THE ARGUMENT, NOT DECORATION. Scrolling walks down the real CE 3.0
 * funnel -- 20,663 chargebacks, 6,244 assessable, 146 clearing the history gate, 3 that
 * qualify -- while the blossom footage darkens and blurs behind it. The numbers come from
 * /api/real/metrics, so the cinematic opening is showing measured results, not a stock
 * video with copy laid over it.
 *
 * PIN LENGTH IS 280%, NOT THE 500% a film-style site would use. A judge evaluating this in
 * two minutes should not have to scroll five screen-heights before reaching the product, so
 * the story lands fast and the live console sits immediately beneath.
 *
 * THREE THINGS THAT BREAK A VIDEO SCRUB, ALL HANDLED:
 *   1. Seeking is asynchronous. Assigning currentTime on every scroll event queues seeks
 *      faster than the decoder retires them and the video freezes. A `seeking` guard drops
 *      frames instead of queueing them.
 *   2. iOS Safari will not seek a video that has never been played. It is primed with a
 *      muted play/pause on first touch.
 *   3. Mobile and reduced-motion get the poster image and no pinning at all -- a pinned,
 *      scrubbing 12 MB video on a phone is a bad trade for everyone.
 */

type Funnel = {
  n_chargebacks: number;
  n_assessable: number;
  funnel: { cleared_prior_gate: number; qualified: number; blocked_no_main_anchor: number };
};

/* Scroll positions at which each scene takes over. Module-level so the animation and the
 * content are driven by one list rather than two that can drift apart. */
const AT = [0, 0.24, 0.44, 0.63, 0.82];

type Scene = {
  at: number;          // scroll progress where this scene takes over
  value?: string;
  label?: string;
  sub?: string;
  kicker?: string;
  final?: boolean;
};

export default function ScrollHero() {
  const wrap = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const overlay = useRef<HTMLVideoElement>(null);
  const sceneEls = useRef<(HTMLDivElement | null)[]>([]);
  const activeRef = useRef<number>(-1);
  const [data, setData] = useState<Funnel | null>(null);
  const [ready, setReady] = useState(false);

  // Whether to run the pinned scrub at all. Evaluated as state and kept in sync with the
  // media queries, because deciding once at mount leaves a resized or rotated viewport
  // stuck in the wrong mode -- mounting narrow and widening left every scene revealed at
  // once, stacked on top of each other.
  // A width of 0 means the viewport has not been measured yet (a detached frame, a hidden
  // tab, a preview pane mid-layout). Treating it as "narrow" drops a desktop visitor into
  // the mobile fallback and never recovers, so it is explicitly excluded.
  const isSimple = () => {
    if (typeof window === "undefined") return false;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return true;
    const w = window.innerWidth;
    return w > 0 && w <= 860;
  };

  const [simple, setSimple] = useState(isSimple);

  useEffect(() => {
    const sync = () => setSimple(isSimple());
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    mq.addEventListener("change", sync);
    window.addEventListener("resize", sync);
    sync();
    return () => {
      mq.removeEventListener("change", sync);
      window.removeEventListener("resize", sync);
    };
  }, []);

  useEffect(() => {
    get<any>("/api/real/metrics")
      .then((d) => d.qualification && setData(d.qualification))
      .catch(() => {});
  }, []);

  // Falls back to the published figures if the API is unreachable, so the hero never
  // renders empty slots.
  const f = data?.funnel;
  const scenes: Scene[] = [
    {
      at: AT[0],
      kicker: "Adjudication Evidence & Genuine-Intent Scoring",
      label: "Most chargebacks are lost months before anyone fights them.",
      sub: "We ran Visa's Compelling Evidence 3.0 gate, unmodified, over real reported chargebacks.",
    },
    {
      at: AT[1],
      value: (data?.n_chargebacks ?? 20663).toLocaleString("en-US"),
      label: "real reported chargebacks",
      sub: "IEEE-CIS, Vesta Corporation. Every one a genuine dispute, not a simulation.",
    },
    {
      at: AT[2],
      value: (data?.n_assessable ?? 6244).toLocaleString("en-US"),
      label: "raised late enough to assess",
      sub: "CE 3.0 needs priors aged 120–364 days. The rest cannot qualify by construction.",
    },
    {
      at: AT[3],
      value: String(f?.cleared_prior_gate ?? 146),
      label: "clear the prior-history gate",
      sub: "Two undisputed transactions on the same credential, inside the window.",
    },
    {
      at: AT[4],
      value: String(f?.qualified ?? 3),
      label: "actually qualify",
      sub: `${f?.blocked_no_main_anchor ?? 49} of the ${f?.cleared_prior_gate ?? 146} that got this far fail for one reason: no IP or device was ever captured.`,
      final: true,
    },
  ];

  useEffect(() => {
    const v = video.current;
    const container = wrap.current;
    if (!v || !container) return;

    if (simple) {
      // No pin, no scrub: the scenes flow down the page normally (see the matching CSS
      // breakpoint, which un-stacks them from absolute positioning).
      setReady(true);
      sceneEls.current.forEach((el) => el?.classList.add("is-active"));
      ScrollTrigger.getById("aegis-hero")?.kill();
      return;
    }

    let st: ScrollTrigger | null = null;
    const timelines: gsap.core.Timeline[] = [];
    let onSeekedOuter: () => void = () => {};

    const build = () => {
      setReady(true);
      const duration = v.duration || 0;

      // Hand sequencing over to JS only once the trigger is genuinely about to exist.
      container.classList.add("is-live");

      // Reset the active index HERE, not only in cleanup. This effect rebuilds when the
      // live figures arrive; if the index still reads 0 from the previous build, the first
      // apply() sees "no change" and skips the fade -- while the fresh gsap.set below has
      // just hidden every scene. The result is a hero with nothing on it at all.
      activeRef.current = -1;

      // This effect runs twice: once on mount, again when the live figures arrive. Killing
      // the timelines in cleanup is not enough -- any in-flight gsap.to on a scene element
      // survives, and keeps animating toward the OLD build's target after the new build has
      // set it to zero. That is what left two or three scenes stranded at partial opacity
      // on top of each other.
      gsap.killTweensOf(sceneEls.current.filter(Boolean));

      sceneEls.current.forEach((el) => {
        if (!el) return;
        const num = el.querySelector(".sh-value");
        const lab = el.querySelector(".sh-label");
        const sub = el.querySelector(".sh-sub");
        const cta = el.querySelector(".sh-cta");

        const tl = gsap.timeline({ paused: true });
        if (num) {
          // Numbers arrive along Z so they read as coming toward the viewer, which is what
          // makes the funnel feel like a descent rather than a slideshow.
          tl.fromTo(num,
            { opacity: 0, z: -420, rotateX: 28, filter: "blur(22px)" },
            { opacity: 1, z: 0, rotateX: 0, filter: "blur(0px)", duration: 1.15, ease: "power3.out" });
        }
        tl.fromTo(lab,
          { opacity: 0, y: 42, filter: "blur(14px)" },
          { opacity: 1, y: 0, filter: "blur(0px)", duration: 1, ease: "power3.out" },
          num ? "-=0.75" : 0);
        if (sub) {
          tl.fromTo(sub,
            { opacity: 0, y: 26, filter: "blur(9px)" },
            { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.9, ease: "power3.out" }, "-=0.62");
        }
        if (cta) {
          tl.fromTo(cta, { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }, "-=0.4");
        }
        timelines.push(tl);
      });

      // Seeking is asynchronous, so assigning currentTime on every scroll event queues
      // seeks faster than the decoder retires them and the picture freezes. This guard
      // drops frames rather than queueing them.
      //
      // The subtlety that broke the first version: seeking to a time the video is ALREADY
      // at fires no `seeked` event. The very first call (target 0, currentTime 0) latched
      // the guard, nothing ever cleared it, and every later seek was silently dropped --
      // the footage sat frozen on frame 0 while the filters animated. Hence the epsilon
      // check before latching, plus a watchdog for seeks the browser drops.
      let seeking = false;
      let watchdog = 0;
      const onSeeked = () => { seeking = false; window.clearTimeout(watchdog); };
      onSeekedOuter = onSeeked;
      v.addEventListener("seeked", onSeeked);

      const seek = (t: number) => {
        if (!duration || seeking) return;
        const target = Math.min(Math.max(t, 0), duration - 0.05);
        if (Math.abs(target - v.currentTime) < 0.04) return;
        seeking = true;
        v.currentTime = target;
        window.clearTimeout(watchdog);
        watchdog = window.setTimeout(() => { seeking = false; }, 320);
      };

      // Scene windows come from the fixed AT marks, not from `scenes` -- the latter is a
      // new array each render and must not be captured by a closure that outlives it.
      const bounds = AT.map((start, i) => ({
        start,
        end: i + 1 < AT.length ? AT[i + 1] : 1.0001,
      }));

      const apply = (p: number) => {
        seek(p * duration);

        // The footage recedes as the funnel narrows: darker, softer, slightly pushed in.
        gsap.set(v, {
          filter: `blur(${(p * 9).toFixed(2)}px) brightness(${(1 - p * 0.62).toFixed(3)}) saturate(${(1 - p * 0.35).toFixed(3)})`,
          scale: 1 + p * 0.14,
        });
        if (overlay.current) {
          // Petal layer fades in over the back half, so the final beat still has motion.
          gsap.set(overlay.current, { opacity: Math.max(0, (p - 0.35)) * 0.62 });
        }

        // Visibility is set directly from progress rather than by play/reverse on the
        // timelines. Reversing stranded scenes at full opacity whenever a scroll crossed
        // several windows in one frame -- three of them ended up stacked on screen at once.
        // Driving opacity from the single source of truth cannot desynchronise.
        const found = bounds.findIndex((b) => p >= b.start && p < b.end);
        const active = found === -1 ? AT.length - 1 : found;

        // Only act on a CHANGE of active scene. Tweening on every frame restarted the
        // fade continuously, so opacity never converged -- three scenes sat at 0.2-0.4 on
        // top of each other instead of one at 1.
        if (active !== activeRef.current) {
          // Visibility is a CSS class toggle, not a GSAP tween.
          //
          // Driving it through gsap.to put the single most important thing on screen behind
          // the JS ticker, and when a tween failed to run the hero rendered completely
          // empty -- the whole page looked dead. A class toggle with a CSS transition is
          // GPU-composited, cannot be starved, and degrades to an instant cut in the worst
          // case rather than to nothing at all. GSAP still drives the inner fly-in, which
          // is decoration and safe to lose.
          sceneEls.current.forEach((el, i) => {
            el?.classList.toggle("is-active", i === active);
          });
          timelines[active]?.restart();
          activeRef.current = active;
        }
      };


      st = ScrollTrigger.create({
        id: "aegis-hero",
        trigger: container,
        start: "top top",
        end: "+=280%",
        pin: true,
        anticipatePin: 1,
        scrub: 0.6,
        onUpdate: (self) => apply(self.progress),
      });
      apply(0);
    };

    // iOS Safari refuses to seek a video that has never been played; a muted play/pause
    // primes the decoder. Silently ignored where autoplay is blocked.
    const prime = () => { v.play().then(() => v.pause()).catch(() => {}); };

    if (v.readyState >= 1) { prime(); build(); }
    else v.addEventListener("loadedmetadata", () => { prime(); build(); }, { once: true });

    return () => {
      v.removeEventListener("seeked", onSeekedOuter);
      container.classList.remove("is-live");
      st?.kill();
      timelines.forEach((t) => t.kill());
      gsap.killTweensOf(sceneEls.current.filter(Boolean));
      ScrollTrigger.getById("aegis-hero")?.kill();
      activeRef.current = -1;
    };
    // Depends ONLY on the layout mode. It deliberately does NOT depend on `data`.
    //
    // Rebuilding the whole ScrollTrigger whenever the live figures arrived was the source
    // of a string of failures: in-flight tweens outliving their build, the active-scene
    // index surviving a teardown so the first apply() skipped the fade, and a torn-down
    // trigger leaving the hero blank. None of it was necessary -- the numbers are just text
    // inside nodes React already owns, and GSAP only ever touches the refs, which persist
    // across a re-render. Build the machinery once; let React update the words.
  }, [simple]);

  return (
    <section className={`sh ${simple ? "is-simple" : ""}`} ref={wrap}>
      <div className="sh-media">
        <video
          ref={video}
          className="sh-video"
          src="/video/hero-scrub.mp4"
          poster="/video/hero-poster.jpg"
          muted
          playsInline
          preload="auto"
          aria-hidden="true"
        />
        <video
          ref={overlay}
          className="sh-overlay-video"
          src="/video/petals-overlay.mp4"
          muted
          loop
          autoPlay
          playsInline
          preload="metadata"
          aria-hidden="true"
        />
        <div className="sh-grade" />
        <div className="sh-vignette" />
      </div>

      {simple ? (
        /* Compact fallback. Stacking five full-height scenes produced a 20,000px page of
           giant numbers with no motion to justify it -- worse on a phone than the desktop
           version it was standing in for. One headline, then the funnel as a tight strip. */
        <div className="sh-simple">
          <div className="sh-kicker">{scenes[0].kicker}</div>
          <div className="sh-label is-headline">{scenes[0].label}</div>
          <div className="sh-sub">{scenes[0].sub}</div>
          <ol className="sh-funnel-strip">
            {scenes.slice(1).map((sc, i) => (
              <li key={i} className={sc.final ? "is-final" : ""}>
                <span className="sh-strip-n">{sc.value}</span>
                <span className="sh-strip-l">{sc.label}</span>
              </li>
            ))}
          </ol>
          <div className="sh-cta">
            <Link to="/console" className="lp-btn lp-btn-primary lp-btn-lg">Open the console</Link>
            <Link to="/console/real" className="lp-btn lp-btn-glass lp-btn-lg">See the working</Link>
          </div>
        </div>
      ) : (
      <div className="sh-scenes">
        {scenes.map((s, i) => (
          <div key={i} className="sh-scene" ref={(el) => (sceneEls.current[i] = el)}>
            {s.kicker && <div className="sh-kicker">{s.kicker}</div>}
            {s.value && (
              <div className={`sh-value ${s.final ? "is-final" : ""}`}>
                {s.value}
                {s.final && <span className="sh-final-tag">qualify</span>}
              </div>
            )}
            <div className={`sh-label ${s.value ? "" : "is-headline"}`}>{s.label}</div>
            {s.sub && <div className="sh-sub">{s.sub}</div>}
            {s.final && (
              <div className="sh-cta">
                <Link to="/console" className="lp-btn lp-btn-primary lp-btn-lg">Open the console</Link>
                <Link to="/console/real" className="lp-btn lp-btn-glass lp-btn-lg">See the working</Link>
              </div>
            )}
          </div>
        ))}
      </div>
      )}

      {ready && !simple && <div className="sh-hint" aria-hidden="true"><span /></div>}
    </section>
  );
}
