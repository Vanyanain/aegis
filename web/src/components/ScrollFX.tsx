import { useEffect } from "react";
import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/* Page-wide scroll feel: smooth scrolling, a petal field that answers to the scroll, and
 * 3D reveals on anything marked up for them.
 *
 * SMOOTH SCROLL. Lenis interpolates toward the native scroll position instead of jumping to
 * it, which is what gives Mercury its weight. It has to be wired into GSAP's ticker rather
 * than its own rAF loop -- running two independent loops leaves ScrollTrigger reading a
 * scroll position one frame behind Lenis, and every pinned element jitters.
 *
 * There is deliberately NO page-wide petal field. A scroll-reactive canvas of petals was
 * built and removed: drifting blossom across tables of chargeback figures competes with the
 * numbers the page exists to show, and decoration that fights the content loses. Petals
 * belong over the hero footage, where they are part of the image rather than on top of it.
 *
 * 3D REVEALS. Anything with data-reveal rises out of Z with a slight X-rotation as it
 * enters. The parent needs a perspective for that to mean anything -- without one a
 * translateZ is silently ignored, which is why most "3D" web animation looks flat.
 *
 * EVERYTHING HERE IS DECORATION AND FAILS SOFT. Reveals start visible in CSS and are only
 * hidden once JS confirms it can animate them; if the script never runs, or the tab is
 * backgrounded where requestAnimationFrame is suspended, the page reads as a normal
 * document instead of a blank one.
 */

export default function ScrollFX() {

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      document.documentElement.classList.add("fx-ready");
      return;
    }

    const lenis = new Lenis({
      duration: 1.05,
      // Slightly overshooting ease: the page settles rather than arriving abruptly.
      easing: (t: number) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      wheelMultiplier: 0.95,
      touchMultiplier: 1.6,
      // Never smooth touch scrolling. Fighting a phone's native scroll physics feels
      // broken in a way no amount of easing fixes.
      syncTouch: false,
    });

    // One loop, not two. Lenis must be driven by GSAP's ticker and ScrollTrigger updated
    // from Lenis, or the two read scroll positions a frame apart and pinned elements jitter.
    lenis.on("scroll", () => ScrollTrigger.update());
    const raf = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(raf);
    gsap.ticker.lagSmoothing(0);

    // Hiding the reveal targets is only safe if the animation frame is genuinely running.
    //
    // Adding this class puts twenty elements at opacity 0 and hands responsibility for
    // showing them to rAF-driven tweens. A backgrounded tab suspends rAF completely --
    // measured at zero frames per second -- so on its own this recreates exactly the
    // failure it is meant to avoid: a page that renders blank. The watchdog below removes
    // the class again if no frame arrives, and the visibility listener restores the effect
    // when the tab is brought forward.
    document.documentElement.classList.add("fx-ready");

    let frames = 0;
    const countFrame = () => { frames++; };
    gsap.ticker.add(countFrame);

    const watchdog = window.setTimeout(() => {
      if (frames < 2) {
        // Nothing is animating. Reveal everything rather than leave the page empty.
        //
        // Dropping the class is NOT enough on its own: gsap.fromTo renders its start state
        // immediately, writing inline opacity:0 onto every target the moment the tween is
        // created. An inline style beats a stylesheet rule, so removing the class left
        // eighteen of twenty elements still invisible. The tweens have to be killed and
        // their inline properties cleared.
        document.documentElement.classList.remove("fx-ready");
        const targets = gsap.utils.toArray<HTMLElement>("[data-reveal]");
        gsap.killTweensOf(targets);
        gsap.set(targets, { clearProps: "all" });
      }
      gsap.ticker.remove(countFrame);
    }, 1200);

    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      document.documentElement.classList.add("fx-ready");
      ScrollTrigger.refresh();
    };
    document.addEventListener("visibilitychange", onVisible);

    const ctx = gsap.context(() => {
      gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((el) => {
        const kind = el.dataset.reveal || "rise";
        const from: gsap.TweenVars =
          kind === "tilt"
            ? { opacity: 0, y: 70, z: -260, rotateX: 14, transformOrigin: "50% 100%" }
            : kind === "swing"
            ? { opacity: 0, y: 40, z: -180, rotateY: -12, transformOrigin: "0% 50%" }
            : { opacity: 0, y: 54, z: -140 };

        gsap.fromTo(el, from, {
          opacity: 1, y: 0, z: 0, rotateX: 0, rotateY: 0,
          duration: 1.05,
          ease: "power3.out",
          scrollTrigger: {
            trigger: el,
            start: "top 88%",
            once: true,
          },
        });
      });

      // Depth: marked layers drift at their own rate against the page.
      gsap.utils.toArray<HTMLElement>("[data-parallax]").forEach((el) => {
        const depth = parseFloat(el.dataset.parallax || "0.12");
        gsap.to(el, {
          yPercent: -depth * 100,
          ease: "none",
          scrollTrigger: {
            trigger: el.closest("section") || el,
            start: "top bottom",
            end: "bottom top",
            scrub: 0.8,
          },
        });
      });
    });

    ScrollTrigger.refresh();

    return () => {
      window.clearTimeout(watchdog);
      gsap.ticker.remove(countFrame);
      document.removeEventListener("visibilitychange", onVisible);
      ctx.revert();
      gsap.ticker.remove(raf);
      lenis.destroy();
      document.documentElement.classList.remove("fx-ready");
    };
  }, []);

  // Nothing to paint: this component only installs behaviour.
  return null;
}
