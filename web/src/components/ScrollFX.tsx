import { useEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/* Scroll behaviour for the landing page: depth, and reveals with a little Z in them.
 *
 * NATIVE SCROLLING, DELIBERATELY. An earlier build ran Lenis for Mercury-style inertial
 * scrolling. It reads as premium on a marketing site and as broken on a tool -- the page
 * keeps gliding after the wheel stops, which fights anyone scanning for a number. Removed.
 * The depth below does the work instead, and it does it without taking the scroll away.
 *
 * NO PARTICLE FIELD EITHER. A scroll-reactive petal canvas was built and removed: drifting
 * blossom over tables of chargeback figures competes with the numbers the page exists to
 * show. Atmosphere belongs in the footage, not on top of the content.
 *
 * DEPTH COMES FROM SPEED DIFFERENCES. Layers marked data-parallax move at their own rate
 * against the page, so foreground and background separate as you scroll. That is what reads
 * as three-dimensional -- not objects flying at the reader.
 *
 * FAILS SOFT. Reveal targets are visible in CSS and only hidden once a frame has genuinely
 * rendered. A backgrounded tab suspends requestAnimationFrame entirely, so tying visibility
 * to rAF-driven tweens renders a blank page; the watchdog below undoes it if no frame
 * arrives, and clears the inline styles gsap.fromTo writes on creation.
 */

export default function ScrollFX() {
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    document.documentElement.classList.add("fx-ready");

    let frames = 0;
    const countFrame = () => { frames++; };
    gsap.ticker.add(countFrame);

    const watchdog = window.setTimeout(() => {
      if (frames < 2) {
        // Nothing is animating. Reveal everything rather than leave the page empty.
        // Dropping the class alone is not enough: gsap.fromTo renders its start state
        // immediately, and an inline opacity:0 beats any stylesheet rule.
        document.documentElement.classList.remove("fx-ready");
        const targets = gsap.utils.toArray<HTMLElement>("[data-reveal]");
        gsap.killTweensOf(targets);
        gsap.set(targets, { clearProps: "all" });
      }
      gsap.ticker.remove(countFrame);
    }, 1200);

    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      ScrollTrigger.refresh();
    };
    document.addEventListener("visibilitychange", onVisible);

    const ctx = gsap.context(() => {
      gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((el) => {
        const kind = el.dataset.reveal || "rise";
        const from: gsap.TweenVars =
          kind === "tilt"
            ? { opacity: 0, y: 64, z: -220, rotateX: 12, transformOrigin: "50% 100%" }
            : kind === "swing"
            ? { opacity: 0, y: 36, z: -160, rotateY: -10, transformOrigin: "0% 50%" }
            : { opacity: 0, y: 46, z: -110 };

        gsap.fromTo(el, from, {
          opacity: 1, y: 0, z: 0, rotateX: 0, rotateY: 0,
          duration: 1, ease: "power3.out",
          scrollTrigger: { trigger: el, start: "top 88%", once: true },
        });
      });

      // Depth. Each marked layer travels at its own rate against the page, so foreground
      // and background pull apart as you scroll.
      gsap.utils.toArray<HTMLElement>("[data-parallax]").forEach((el) => {
        const depth = parseFloat(el.dataset.parallax || "0.12");
        gsap.fromTo(el,
          { yPercent: depth * 50 },
          {
            yPercent: -depth * 50, ease: "none",
            scrollTrigger: {
              trigger: el.closest("section") || el,
              start: "top bottom", end: "bottom top", scrub: 0.6,
            },
          });
      });

      // Full-bleed footage sections: the video drifts slower than the page and brightens
      // as the section reaches centre, so scrolling moves THROUGH a scene rather than past
      // a background image.
      gsap.utils.toArray<HTMLElement>(".scene-media").forEach((el) => {
        gsap.fromTo(el,
          { scale: 1.16, filter: "brightness(0.5) saturate(0.7)" },
          {
            scale: 1.0, filter: "brightness(0.82) saturate(1)", ease: "none",
            scrollTrigger: {
              trigger: el.closest("section") || el,
              start: "top bottom", end: "center center", scrub: 0.7,
            },
          });
      });

      // Light. A bloom behind each scene heading swells as it centres.
      gsap.utils.toArray<HTMLElement>(".scene-glow").forEach((el) => {
        gsap.fromTo(el,
          { opacity: 0, scale: 0.6 },
          {
            opacity: 1, scale: 1.25, ease: "none",
            scrollTrigger: {
              trigger: el.closest("section") || el,
              start: "top bottom", end: "bottom top", scrub: 1,
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
      document.documentElement.classList.remove("fx-ready");
    };
  }, []);

  // Installs behaviour only; renders nothing.
  return null;
}
